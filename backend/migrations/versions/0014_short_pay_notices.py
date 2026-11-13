"""P3-02: persisted A5 short-pay notices and the vendor's single Accept/Contest response.

The API (decision graph + vendor-sim router) inserts a notice and records one response. It gets
no DELETE and may update only the response columns; an answered notice is frozen for every role.
The API write guard gains exactly one exception on settlement-state executions: the vendor's
NOTICE_SENT -> ACCEPTED|CONTESTED with every other column unchanged; otherwise the API can never
set the balance (settlement sends the notice, §11.4). Nothing about vendor_master, payouts,
outbox or ledgers changes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_EVENTS = "'HALT','VENDOR_CHANGE','MANDATE_ACTIVATED','WEBHOOK_SIGNATURE_FAILURE','APPROVAL_OUTSIDE_HOURS'"
# §12.4: the vendor's single response, and nothing else, may touch a settlement-state row.
_VENDOR_RESPONSE = """
                    IF TG_OP='UPDATE' AND OLD.short_pay_balance='NOTICE_SENT'
                       AND NEW.short_pay_balance IN ('ACCEPTED','CONTESTED')
                       AND (to_jsonb(NEW) - 'short_pay_balance') = (to_jsonb(OLD) - 'short_pay_balance') THEN
                        RETURN NEW;
                    END IF;
                    IF (TG_OP='INSERT' AND COALESCE(NEW.short_pay_balance,'NONE')<>'NONE')
                       OR (TG_OP='UPDATE' AND NEW.short_pay_balance IS DISTINCT FROM OLD.short_pay_balance) THEN
                        RAISE EXCEPTION 'API cannot set the short-pay balance (R22)' USING ERRCODE='integrity_constraint_violation';
                    END IF;"""


def _guard(extra: str) -> str:
    """Migration 0006's API write guard with `extra` first in the execution branch."""
    return f"""
        CREATE OR REPLACE FUNCTION aegis_api_write_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN
            IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
               AND pg_has_role(current_user,'aegis_api_role','USAGE') THEN
                IF TG_TABLE_NAME='settlement_outbox' AND NEW.status<>'PENDING' THEN
                    RAISE EXCEPTION 'API can only enqueue pending commands (R22)' USING ERRCODE='integrity_constraint_violation';
                END IF;
                IF TG_TABLE_NAME='invoice_executions' THEN{extra}
                    IF NEW.status NOT IN ('RECEIVED','ANALYZING','FROZEN','REJECTED','DUPLICATE_BLOCKED','APPROVED_FOR_PAYMENT')
                       OR (TG_OP='UPDATE' AND OLD.status IN ('PAYOUT_SENDING','PAYOUT_SUBMITTED','PAYOUT_SUCCEEDED','PAYOUT_FAILED','SETTLED','NEEDS_MANUAL_REVIEW')) THEN
                        RAISE EXCEPTION 'API cannot write settlement state (R22)' USING ERRCODE='integrity_constraint_violation';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """


def _alert_check(events: str, *, valid: bool = True) -> None:
    op.drop_constraint("ops_alerts_event_check", "ops_alerts", type_="check")
    op.execute(
        f"ALTER TABLE ops_alerts ADD CONSTRAINT ops_alerts_event_check CHECK (event IN ({events}))"
        + ("" if valid else " NOT VALID")  # downgrade keeps already-raised alerts as history
    )


def upgrade():
    op.create_table(
        "short_pay_notices",
        sa.Column("notice_id", sa.Text(), primary_key=True),
        sa.Column(
            "execution_id",
            sa.Text(),
            sa.ForeignKey("invoice_executions.execution_id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("delta_cents", sa.BigInteger(), nullable=False),
        sa.Column("used_fallback", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("responded_by", sa.Text(), sa.ForeignKey("operators.operator_id")),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("delta_cents > 0", name="ck_notice_delta"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="ck_notice_text"),
        sa.CheckConstraint(
            "reason IS NULL OR (response IS NOT NULL AND char_length(reason) <= 1000)",
            name="ck_notice_reason",
        ),
        sa.CheckConstraint(
            "response IS NULL OR response IN ('ACCEPTED','CONTESTED')", name="ck_notice_response"
        ),
        sa.CheckConstraint(
            "(response IS NULL) = (responded_at IS NULL)"
            " AND (response IS NULL) = (responded_by IS NULL)",
            name="ck_notice_responded",
        ),
    )
    op.execute("GRANT SELECT, INSERT ON short_pay_notices TO aegis_api_role")
    op.execute(
        "GRANT UPDATE(response,reason,responded_by,responded_at) ON short_pay_notices"
        " TO aegis_api_role"
    )
    op.execute("GRANT SELECT ON short_pay_notices TO aegis_settlement_role")
    op.execute("""
        CREATE FUNCTION aegis_notice_response_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.response IS NOT NULL
               OR (NEW.notice_id, NEW.execution_id, NEW.text, NEW.delta_cents, NEW.used_fallback,
                   NEW.created_at)
                  IS DISTINCT FROM
                  (OLD.notice_id, OLD.execution_id, OLD.text, OLD.delta_cents, OLD.used_fallback,
                   OLD.created_at) THEN
                RAISE EXCEPTION 'a short-pay notice takes one response and is otherwise immutable'
                    USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_notice_response_guard BEFORE UPDATE ON short_pay_notices"
        " FOR EACH ROW EXECUTE FUNCTION aegis_notice_response_guard()"
    )
    op.execute(_guard(_VENDOR_RESPONSE))
    _alert_check(_EVENTS + ",'SHORT_PAY_NOTICE_MISSING'")


def downgrade():
    _alert_check(_EVENTS, valid=False)
    op.execute(_guard(""))
    op.drop_table("short_pay_notices")
    op.execute("DROP FUNCTION aegis_notice_response_guard()")
