"""P3-02: persisted A5 short-pay notices and the vendor's single Accept/Contest response.

The API (decision graph + vendor-sim router) inserts a notice and records one response. It gets
no DELETE and may update only the response columns; text and delta are immutable to it. Nothing
about vendor_master, payouts, outbox or ledgers changes.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


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


def downgrade():
    op.drop_table("short_pay_notices")
