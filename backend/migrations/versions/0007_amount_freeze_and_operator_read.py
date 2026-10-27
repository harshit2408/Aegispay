"""Bound approved_cents by billed_cents, freeze billed_cents, hide operators from settlement (R3, R22).

approved_cents is already write-once (0004). Review findings on 0006: billed_cents stayed writable
after approval and nothing tied approved_cents to it; settlement could read operators unused.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

BINDINGS = (
    "document_id,po_id,vendor_id,paypal_invoice_id,policy_id,policy_version,"
    "vendor_version,po_version,grn_id"
)


def _binding_guard(extra: str) -> str:
    return f"""
        CREATE OR REPLACE FUNCTION aegis_execution_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.approved_cents IS NOT NULL AND (
               ({",".join("NEW." + c for c in BINDINGS.split(","))})
               IS DISTINCT FROM
               ({",".join("OLD." + c for c in BINDINGS.split(","))}){extra}) THEN
                RAISE EXCEPTION 'approved execution bindings are frozen (R5,R20)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF NEW.approved_cents IS NOT NULL
               AND (NEW.billed_cents IS NULL OR NEW.approved_cents > NEW.billed_cents) THEN
                RAISE EXCEPTION 'approved_cents must not exceed billed_cents (R3)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """


def upgrade() -> None:
    # R3: billed is fixed once approved, and the approved amount never exceeds it.
    op.execute(
        _binding_guard(
            "\n               OR NEW.approved_cents IS NULL OR NEW.approved_cents > OLD.approved_cents"
            "\n               OR NEW.billed_cents IS DISTINCT FROM OLD.billed_cents"
        )
    )
    op.execute("REVOKE SELECT ON operators FROM aegis_settlement_role")


def downgrade() -> None:
    op.execute(_binding_guard(""))
    op.execute("GRANT SELECT ON operators TO aegis_settlement_role")
