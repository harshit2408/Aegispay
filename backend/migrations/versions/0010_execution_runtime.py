"""Execution admission rate limit, payout isolation and committed status notifications."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE SCHEMA IF NOT EXISTS aegis_checkpoints AUTHORIZATION aegis_api_role
    """)
    op.execute("""
    ALTER TABLE operator_sessions ADD COLUMN upload_window_started timestamptz
    """)
    op.execute("""
    ALTER TABLE operator_sessions ADD COLUMN upload_count integer NOT NULL DEFAULT 0
    """)
    op.execute("""
    GRANT INSERT(po_id,execution_id,kind,qty,amount_cents) ON po_ledger TO aegis_api_role
    """)
    op.execute("""
    GRANT USAGE ON SEQUENCE po_ledger_entry_id_seq TO aegis_api_role
    """)
    op.execute("""
    CREATE FUNCTION public.aegis_lock_vendor(text) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT EXISTS (SELECT 1 FROM public.vendor_master WHERE vendor_id=$1 FOR UPDATE)
    $$
    """)
    op.execute("""
    REVOKE EXECUTE ON FUNCTION public.aegis_lock_vendor(text) FROM PUBLIC
    """)
    op.execute("""
    GRANT EXECUTE ON FUNCTION public.aegis_lock_vendor(text) TO aegis_api_role
    """)
    op.execute("""
    CREATE FUNCTION aegis_live_money_only() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF EXISTS (SELECT 1 FROM public.invoice_executions
                   WHERE execution_id=NEW.execution_id AND replayed) THEN
            RAISE EXCEPTION 'replay execution cannot write a live money command';
        END IF;
        RETURN NEW;
    END;
    $$
    """)
    op.execute("""
    CREATE TRIGGER aegis_outbox_no_replay BEFORE INSERT ON settlement_outbox
        FOR EACH ROW EXECUTE FUNCTION aegis_live_money_only()
    """)
    op.execute("""
    CREATE TRIGGER aegis_po_no_replay BEFORE INSERT ON po_ledger
        FOR EACH ROW EXECUTE FUNCTION aegis_live_money_only()
    """)
    op.execute("""
    CREATE FUNCTION aegis_execution_notice() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        PERFORM pg_notify('aegis_status', json_build_object(
            'type', CASE WHEN NEW.status LIKE 'PAYOUT%' OR NEW.status='SETTLED'
                         THEN 'settlement' ELSE 'node' END,
            'execution_id', NEW.execution_id, 'name', 'execution_status',
            'status', NEW.status, 'ts', statement_timestamp()
        )::text);
        RETURN NEW;
    END;
    $$
    """)
    op.execute("""
    CREATE TRIGGER aegis_notify_execution AFTER INSERT OR UPDATE OF row_version ON invoice_executions
        FOR EACH ROW EXECUTE FUNCTION aegis_execution_notice()
    """)


def downgrade():
    op.execute("""
    DROP TRIGGER aegis_notify_execution ON invoice_executions
    """)
    op.execute("""
    DROP FUNCTION aegis_execution_notice()
    """)
    op.execute("""
    DROP TRIGGER aegis_po_no_replay ON po_ledger
    """)
    op.execute("""
    DROP TRIGGER aegis_outbox_no_replay ON settlement_outbox
    """)
    op.execute("""
    DROP FUNCTION aegis_live_money_only()
    """)
    op.execute("""
    DROP FUNCTION public.aegis_lock_vendor(text)
    """)
    op.execute("""
    REVOKE INSERT(po_id,execution_id,kind,qty,amount_cents) ON po_ledger FROM aegis_api_role
    """)
    op.execute("""
    REVOKE USAGE ON SEQUENCE po_ledger_entry_id_seq FROM aegis_api_role
    """)
    op.execute("""
    ALTER TABLE operator_sessions DROP COLUMN upload_count, DROP COLUMN upload_window_started
    """)
