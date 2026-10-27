"""Trust-root immutability, receiving/approval SoD and runtime grants (R11, R15-R18, R22).

Capability roles carry no passwords or login. Provision distinct restricted login members outside
Alembic; the migration owner is never a runtime connection. P2 vendor application remains owner-only.
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

APPEND_ONLY = ("goods_receipts", "po_versions", "spend_ledger", "po_ledger")


def upgrade() -> None:
    op.create_check_constraint(
        "ck_poled_ref", "po_ledger", "(kind = 'RELEASE') = (reserve_entry_id IS NOT NULL)"
    )
    op.create_check_constraint("ck_vendor_version", "vendor_master", "version > 0")
    op.create_check_constraint(
        "ck_vendor_evidence",
        "vendor_master",
        "jsonb_typeof(onboarding_evidence) = 'object' AND onboarding_evidence <> '{}'::jsonb",
    )
    op.create_index("uq_grn_one_correction", "goods_receipts", ["supersedes_grn_id"], unique=True)
    op.execute("""
        CREATE FUNCTION aegis_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE='integrity_constraint_violation';
        END $$
    """)
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION aegis_append_only()"
        )
    op.execute("""
        CREATE FUNCTION aegis_grn_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        DECLARE po purchase_orders%ROWTYPE; recorder operators%ROWTYPE; accepted bigint;
        BEGIN
            SELECT * INTO STRICT po FROM purchase_orders WHERE po_id=NEW.po_id FOR UPDATE;
            SELECT * INTO STRICT recorder FROM operators WHERE operator_id=NEW.recorded_by;
            IF recorder.disabled OR NEW.recorded_by=po.created_by
               OR NOT (recorder.role='receiver' OR (NEW.source='SEEDED' AND recorder.role='admin')) THEN
                RAISE EXCEPTION 'GRN recorder violates receiving SoD (R16)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF NEW.supersedes_grn_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM goods_receipts g WHERE g.grn_id=NEW.supersedes_grn_id AND g.po_id=NEW.po_id
                AND NOT EXISTS (SELECT 1 FROM goods_receipts c WHERE c.supersedes_grn_id=g.grn_id)
            ) THEN
                RAISE EXCEPTION 'GRN correction must supersede the current same-PO receipt' USING ERRCODE='integrity_constraint_violation';
            END IF;
            SELECT COALESCE(sum(g.accepted_qty),0) INTO accepted FROM goods_receipts g
            WHERE g.po_id=NEW.po_id AND g.grn_id IS DISTINCT FROM NEW.supersedes_grn_id
              AND NOT EXISTS (SELECT 1 FROM goods_receipts c WHERE c.supersedes_grn_id=g.grn_id);
            IF accepted+NEW.accepted_qty > po.ordered_qty THEN
                RAISE EXCEPTION 'GRN acceptance exceeds ordered quantity (R16)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_grn_guard BEFORE INSERT ON goods_receipts FOR EACH ROW EXECUTE FUNCTION aegis_grn_guard()"
    )
    op.execute("""
        CREATE FUNCTION aegis_mandate_sod_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM operators WHERE operator_id=NEW.created_by AND role='controller' AND NOT disabled)
               OR (NEW.activated_by IS NOT NULL AND NOT EXISTS (SELECT 1 FROM operators WHERE operator_id=NEW.activated_by AND role='cfo' AND NOT disabled))
               OR (NEW.is_active AND (NEW.activated_by IS NULL OR NEW.activated_at IS NULL)) THEN
                RAISE EXCEPTION 'mandate author/activator violates SoD (R13,R18)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_mandate_sod_guard BEFORE INSERT OR UPDATE ON mandate_policies FOR EACH ROW EXECUTE FUNCTION aegis_mandate_sod_guard()"
    )
    op.execute("""
        CREATE FUNCTION aegis_approval_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'approval history cannot be deleted' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF TG_OP='UPDATE' THEN
                IF (to_jsonb(NEW)-'voided_at'-'consumed_at') IS DISTINCT FROM (to_jsonb(OLD)-'voided_at'-'consumed_at')
                   OR (OLD.voided_at IS NOT NULL AND NEW.voided_at IS DISTINCT FROM OLD.voided_at)
                   OR (OLD.consumed_at IS NOT NULL AND NEW.consumed_at IS DISTINCT FROM OLD.consumed_at) THEN
                    RAISE EXCEPTION 'approval vote is immutable (R11)' USING ERRCODE='integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM operators WHERE operator_id=NEW.actor AND role IN ('controller','cfo') AND NOT disabled)
               OR EXISTS (
                 SELECT 1 FROM invoice_executions e JOIN invoice_documents d USING(document_id)
                 LEFT JOIN purchase_orders p USING(po_id) LEFT JOIN goods_receipts g ON g.grn_id=e.grn_id
                 WHERE e.execution_id=NEW.execution_id AND NEW.actor IN(d.uploaded_by,p.created_by,g.recorded_by)
               ) THEN
                RAISE EXCEPTION 'approval violates operator SoD (R11,R18)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_approval_guard BEFORE INSERT OR UPDATE OR DELETE ON approvals FOR EACH ROW EXECUTE FUNCTION aegis_approval_guard()"
    )
    op.execute("""
        CREATE FUNCTION aegis_vendor_change_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'vendor change history cannot be deleted (R15)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF TG_OP='UPDATE' THEN
                IF (to_jsonb(NEW)-'status'-'approved_by'-'approved_at'-'effective_at') IS DISTINCT FROM
                   (to_jsonb(OLD)-'status'-'approved_by'-'approved_at'-'effective_at')
                   OR (OLD.approved_by IS NOT NULL AND (NEW.approved_by,NEW.approved_at,NEW.effective_at) IS DISTINCT FROM (OLD.approved_by,OLD.approved_at,OLD.effective_at))
                   OR NOT ((OLD.status='REQUESTED' AND NEW.status IN ('COOLING','REJECTED')) OR (OLD.status='COOLING' AND NEW.status='EFFECTIVE')) THEN
                    RAISE EXCEPTION 'vendor change values/history are immutable (R15)' USING ERRCODE='integrity_constraint_violation';
                END IF;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM operators WHERE operator_id=NEW.requested_by AND role='vendor_admin' AND NOT disabled) THEN
                RAISE EXCEPTION 'vendor requester must be vendor_admin (R15)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF NEW.status IN ('COOLING','EFFECTIVE') AND (
                 NEW.approved_by IS NULL OR NEW.approved_by=NEW.requested_by OR NEW.approved_at IS NULL OR NEW.effective_at IS NULL
                 OR NEW.approved_at < NEW.created_at OR NEW.approved_at > now() OR NEW.effective_at < NEW.approved_at+interval '72 hours'
                 OR NOT EXISTS (SELECT 1 FROM operators WHERE operator_id=NEW.approved_by AND role IN ('vendor_admin','cfo') AND NOT disabled)
                 OR (NEW.status='EFFECTIVE' AND NEW.effective_at > now())) THEN
                RAISE EXCEPTION 'vendor approval or cooling period invalid (R15)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF NEW.status IN ('REQUESTED','REJECTED') AND (NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL OR NEW.effective_at IS NOT NULL) THEN
                RAISE EXCEPTION 'unapproved vendor change carries approval metadata' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_vendor_change_guard BEFORE INSERT OR UPDATE OR DELETE ON vendor_master_changes FOR EACH ROW EXECUTE FUNCTION aegis_vendor_change_guard()"
    )
    op.execute("""
        CREATE FUNCTION aegis_execution_binding_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.approved_cents IS NOT NULL AND
               (NEW.document_id,NEW.po_id,NEW.vendor_id,NEW.paypal_invoice_id,NEW.policy_id,NEW.policy_version,NEW.vendor_version,NEW.po_version,NEW.grn_id)
               IS DISTINCT FROM
               (OLD.document_id,OLD.po_id,OLD.vendor_id,OLD.paypal_invoice_id,OLD.policy_id,OLD.policy_version,OLD.vendor_version,OLD.po_version,OLD.grn_id) THEN
                RAISE EXCEPTION 'approved execution bindings are frozen (R5,R20)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_execution_binding_guard BEFORE UPDATE ON invoice_executions FOR EACH ROW EXECUTE FUNCTION aegis_execution_binding_guard()"
    )
    for role in ("aegis_api_role", "aegis_settlement_role"):
        op.execute(f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN
                    CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
                ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname='{role}' AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
                    RAISE EXCEPTION 'unsafe existing capability role {role}';
                END IF;
            END $$
        """)
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC, aegis_api_role, aegis_settlement_role"
    )
    op.execute(
        "REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC, aegis_api_role, aegis_settlement_role"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO aegis_api_role, aegis_settlement_role")
    op.execute(
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO aegis_api_role, aegis_settlement_role"
    )
    op.execute(
        "GRANT INSERT ON invoice_documents, invoice_executions, approvals, audit_queue, webhook_events TO aegis_api_role"
    )
    op.execute("GRANT UPDATE ON invoice_executions, approvals TO aegis_api_role")
    op.execute(
        "GRANT UPDATE(extraction_json,forensics_json) ON invoice_documents TO aegis_api_role"
    )
    op.execute("GRANT UPDATE(failed_logins,locked_until) ON operators TO aegis_api_role")
    op.execute(
        "GRANT INSERT(command_id,execution_id,amount_cents,receiver_payer_id,vendor_version,status) ON settlement_outbox TO aegis_api_role"
    )
    op.execute("GRANT INSERT, UPDATE ON payouts TO aegis_settlement_role")
    op.execute(
        "GRANT UPDATE ON invoice_executions, settlement_outbox, webhook_events, settlement_controls TO aegis_settlement_role"
    )
    op.execute(
        "GRANT INSERT ON spend_ledger, po_ledger, execution_events, audit_anchors TO aegis_settlement_role"
    )
    op.execute("GRANT DELETE ON audit_queue TO aegis_settlement_role")
    op.execute("GRANT UPDATE(status) ON purchase_orders TO aegis_settlement_role")
    # Postgres requires UPDATE on a column even for SELECT FOR NO KEY UPDATE (R8).
    op.execute("GRANT UPDATE(policy_id) ON mandate_policies TO aegis_settlement_role")
    op.execute("GRANT USAGE ON SEQUENCE audit_queue_queue_seq_seq TO aegis_api_role")
    op.execute(
        "GRANT USAGE ON SEQUENCE spend_ledger_entry_id_seq, po_ledger_entry_id_seq, execution_events_event_seq_seq, audit_anchors_anchor_id_seq TO aegis_settlement_role"
    )
    op.execute("""
        CREATE FUNCTION aegis_api_write_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
        BEGIN
            IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
               AND pg_has_role(current_user,'aegis_api_role','USAGE') THEN
                IF TG_TABLE_NAME='settlement_outbox' AND NEW.status<>'PENDING' THEN
                    RAISE EXCEPTION 'API can only enqueue pending commands (R22)' USING ERRCODE='integrity_constraint_violation';
                END IF;
                IF TG_TABLE_NAME='invoice_executions' THEN
                    IF NEW.status NOT IN ('RECEIVED','ANALYZING','FROZEN','REJECTED','DUPLICATE_BLOCKED','APPROVED_FOR_PAYMENT')
                       OR (TG_OP='UPDATE' AND OLD.status IN ('PAYOUT_SENDING','PAYOUT_SUBMITTED','PAYOUT_SUCCEEDED','PAYOUT_FAILED','SETTLED','NEEDS_MANUAL_REVIEW')) THEN
                        RAISE EXCEPTION 'API cannot write settlement state (R22)' USING ERRCODE='integrity_constraint_violation';
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_api_execution_guard BEFORE INSERT OR UPDATE ON invoice_executions FOR EACH ROW EXECUTE FUNCTION aegis_api_write_guard()"
    )
    op.execute(
        "CREATE TRIGGER trg_api_outbox_guard BEFORE INSERT ON settlement_outbox FOR EACH ROW EXECUTE FUNCTION aegis_api_write_guard()"
    )
    op.execute("""
        CREATE FUNCTION aegis_settlement_mandate_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
               AND pg_has_role(current_user,'aegis_settlement_role','USAGE') THEN
                RAISE EXCEPTION 'settlement may lock but never edit mandate policies (R22)' USING ERRCODE='integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_settlement_mandate_guard BEFORE UPDATE ON mandate_policies FOR EACH ROW EXECUTE FUNCTION aegis_settlement_mandate_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_settlement_mandate_guard ON mandate_policies")
    op.execute("DROP FUNCTION aegis_settlement_mandate_guard()")
    op.execute("DROP TRIGGER trg_api_outbox_guard ON settlement_outbox")
    op.execute("DROP TRIGGER trg_api_execution_guard ON invoice_executions")
    op.execute("DROP FUNCTION aegis_api_write_guard()")
    for role in ("aegis_api_role", "aegis_settlement_role"):
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON SCHEMA public FROM {role}")
    op.execute("REVOKE UPDATE(policy_id) ON mandate_policies FROM aegis_settlement_role")
    op.execute("REVOKE UPDATE(status) ON purchase_orders FROM aegis_settlement_role")
    op.execute(
        "REVOKE UPDATE(extraction_json,forensics_json) ON invoice_documents FROM aegis_api_role"
    )
    op.execute("REVOKE UPDATE(failed_logins,locked_until) ON operators FROM aegis_api_role")
    op.execute(
        "REVOKE INSERT(command_id,execution_id,amount_cents,receiver_payer_id,vendor_version,status) ON settlement_outbox FROM aegis_api_role"
    )
    for table, trigger, function in (
        ("invoice_executions", "trg_execution_binding_guard", "aegis_execution_binding_guard"),
        ("vendor_master_changes", "trg_vendor_change_guard", "aegis_vendor_change_guard"),
        ("approvals", "trg_approval_guard", "aegis_approval_guard"),
        ("goods_receipts", "trg_grn_guard", "aegis_grn_guard"),
        ("mandate_policies", "trg_mandate_sod_guard", "aegis_mandate_sod_guard"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    for table in APPEND_ONLY:
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION aegis_append_only()")
    op.drop_index("uq_grn_one_correction", "goods_receipts")
    op.drop_constraint("ck_vendor_evidence", "vendor_master", type_="check")
    op.drop_constraint("ck_vendor_version", "vendor_master", type_="check")
    op.drop_constraint("ck_poled_ref", "po_ledger", type_="check")
