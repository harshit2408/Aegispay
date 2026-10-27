"""immutability triggers for the frozen settlement command (PRD §8, R5, R24)

settlement_outbox: execution, amount, receiver and vendor version never change once written.
invoice_executions: approved_cents is write-once; replayed never changes.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION aegis_outbox_command_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.execution_id IS DISTINCT FROM OLD.execution_id
               OR NEW.amount_cents IS DISTINCT FROM OLD.amount_cents
               OR NEW.receiver_payer_id IS DISTINCT FROM OLD.receiver_payer_id
               OR NEW.vendor_version IS DISTINCT FROM OLD.vendor_version THEN
                RAISE EXCEPTION 'settlement_outbox command is immutable (R5)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_outbox_command_immutable BEFORE UPDATE ON settlement_outbox "
        "FOR EACH ROW EXECUTE FUNCTION aegis_outbox_command_immutable()"
    )
    op.execute(
        """
        CREATE FUNCTION aegis_execution_write_once() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.approved_cents IS NOT NULL
               AND NEW.approved_cents IS DISTINCT FROM OLD.approved_cents THEN
                RAISE EXCEPTION 'invoice_executions.approved_cents is write-once (R5)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.replayed IS DISTINCT FROM OLD.replayed THEN
                RAISE EXCEPTION 'invoice_executions.replayed is immutable (R24)'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_write_once BEFORE UPDATE ON invoice_executions "
        "FOR EACH ROW EXECUTE FUNCTION aegis_execution_write_once()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_execution_write_once ON invoice_executions")
    op.execute("DROP FUNCTION aegis_execution_write_once()")
    op.execute("DROP TRIGGER trg_outbox_command_immutable ON settlement_outbox")
    op.execute("DROP FUNCTION aegis_outbox_command_immutable()")
