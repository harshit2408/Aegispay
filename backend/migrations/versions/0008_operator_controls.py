"""P2 sessions, alert outbox and least-privilege control routes (R15,R16,R18,R22,R25)."""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    for statement in """
        CREATE TABLE operator_sessions (
          token_hash TEXT PRIMARY KEY, operator_id TEXT NOT NULL REFERENCES operators(operator_id),
          created_at TIMESTAMPTZ NOT NULL, last_seen_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL);
        CREATE TABLE auth_attempts (
          bucket_key TEXT PRIMARY KEY, window_started TIMESTAMPTZ NOT NULL,
          attempts INTEGER NOT NULL CHECK(attempts >= 0));
        CREATE TABLE ops_alerts (
          alert_id TEXT PRIMARY KEY,
          event TEXT NOT NULL CHECK(event IN ('HALT','VENDOR_CHANGE','MANDATE_ACTIVATED','WEBHOOK_SIGNATURE_FAILURE','APPROVAL_OUTSIDE_HOURS')),
          execution_id TEXT, created_at TIMESTAMPTZ NOT NULL, delivered_at TIMESTAMPTZ);
        GRANT SELECT, INSERT, UPDATE, DELETE ON operator_sessions, auth_attempts TO aegis_api_role;
        GRANT SELECT, INSERT ON ops_alerts TO aegis_api_role, aegis_settlement_role;
        GRANT UPDATE(delivered_at) ON ops_alerts TO aegis_settlement_role;
        GRANT INSERT ON goods_receipts, mandate_policies, vendor_master_changes TO aegis_api_role;
        GRANT UPDATE(is_active,activated_by,activated_at) ON mandate_policies TO aegis_api_role;
        GRANT UPDATE(status,approved_by,approved_at,effective_at) ON vendor_master_changes TO aegis_api_role;
        GRANT UPDATE ON settlement_controls TO aegis_api_role;
        GRANT UPDATE ON vendor_master TO aegis_settlement_role;
        GRANT UPDATE(status) ON vendor_master_changes TO aegis_settlement_role;
        CREATE UNIQUE INDEX uq_vendor_pending_change ON vendor_master_changes(vendor_id)
          WHERE status IN ('REQUESTED','COOLING');
    """.split(";"):
        if statement.strip():
            op.execute(statement)
    # Row-lock grants do not permit writes: guards reject edits and R22 API master writes remain absent.
    op.execute("GRANT UPDATE(po_id) ON purchase_orders TO aegis_api_role")
    op.execute("""
        CREATE FUNCTION aegis_api_lock_only_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
             AND pg_has_role(current_user,'aegis_api_role','USAGE') THEN
            RAISE EXCEPTION 'API may lock but never edit purchase orders (R22)' USING ERRCODE='integrity_constraint_violation';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_api_po_lock_only BEFORE UPDATE ON purchase_orders FOR EACH ROW EXECUTE FUNCTION aegis_api_lock_only_guard()"
    )


def downgrade():
    op.execute("DROP TRIGGER trg_api_po_lock_only ON purchase_orders")
    op.execute("DROP FUNCTION aegis_api_lock_only_guard()")
    op.execute("REVOKE UPDATE(po_id) ON purchase_orders FROM aegis_api_role")
    op.execute("REVOKE UPDATE ON vendor_master FROM aegis_settlement_role")
    op.execute("REVOKE UPDATE(status) ON vendor_master_changes FROM aegis_settlement_role")
    op.execute("REVOKE UPDATE ON settlement_controls FROM aegis_api_role")
    op.execute(
        "REVOKE UPDATE(is_active,activated_by,activated_at) ON mandate_policies FROM aegis_api_role"
    )
    op.execute(
        "REVOKE UPDATE(status,approved_by,approved_at,effective_at) ON vendor_master_changes FROM aegis_api_role"
    )
    op.execute(
        "REVOKE INSERT ON goods_receipts, mandate_policies, vendor_master_changes FROM aegis_api_role"
    )
    op.execute("DROP INDEX uq_vendor_pending_change")
    for table in ("ops_alerts", "auth_attempts", "operator_sessions"):
        op.drop_table(table)
