"""R15 restricts settlement vendor edits to independently approved change records."""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("REVOKE UPDATE ON vendor_master FROM aegis_settlement_role")
    op.execute(
        "GRANT UPDATE(paypal_payer_id,verified_paypal_email,trust_status,version,"
        "payee_last_changed_at) ON vendor_master TO aegis_settlement_role"
    )
    op.execute("""
        CREATE FUNCTION aegis_vendor_master_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
        DECLARE
            change public.vendor_master_changes%ROWTYPE;
        BEGIN
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                RETURN NEW;
            END IF;
            IF NOT pg_has_role(current_user,'aegis_settlement_role','USAGE')
               OR OLD.trust_status='BLOCKED'
               OR (to_jsonb(NEW)-ARRAY['paypal_payer_id','verified_paypal_email',
                   'trust_status','version','payee_last_changed_at'])
                  IS DISTINCT FROM
                  (to_jsonb(OLD)-ARRAY['paypal_payer_id','verified_paypal_email',
                   'trust_status','version','payee_last_changed_at']) THEN
                RAISE EXCEPTION 'vendor trust roots require approved change (R15)'
                    USING ERRCODE='integrity_constraint_violation';
            END IF;
            SELECT * INTO change FROM public.vendor_master_changes
              WHERE vendor_id=OLD.vendor_id AND status IN ('COOLING','EFFECTIVE')
                AND approved_by IS NOT NULL AND approved_by<>requested_by
                AND approved_at IS NOT NULL AND effective_at>=approved_at+interval '72 hours'
                AND (old_values-'version')=jsonb_build_object(
                    'paypal_payer_id',OLD.paypal_payer_id,
                    'verified_paypal_email',OLD.verified_paypal_email)
                AND (NOT old_values ? 'version' OR old_values->>'version'=OLD.version::text)
              ORDER BY created_at DESC LIMIT 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'vendor update has no matching approval (R15)'
                    USING ERRCODE='integrity_constraint_violation';
            END IF;
            IF NEW.trust_status='COOLING' AND change.status='COOLING'
               AND (NEW.paypal_payer_id,NEW.verified_paypal_email,NEW.version,
                    NEW.payee_last_changed_at)
                   IS NOT DISTINCT FROM
                   (OLD.paypal_payer_id,OLD.verified_paypal_email,OLD.version,
                    OLD.payee_last_changed_at) THEN
                RETURN NEW;
            END IF;
            IF change.effective_at<=now()
               AND change.new_values=jsonb_build_object(
                    'paypal_payer_id',NEW.paypal_payer_id,
                    'verified_paypal_email',NEW.verified_paypal_email)
               AND NEW.version=OLD.version+1
               AND NEW.trust_status='VERIFIED'
               AND NEW.payee_last_changed_at>=change.effective_at
               AND NEW.payee_last_changed_at<=clock_timestamp() THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'vendor update differs from approved values or cooling (R15)'
                USING ERRCODE='integrity_constraint_violation';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER trg_vendor_master_guard BEFORE UPDATE ON vendor_master "
        "FOR EACH ROW EXECUTE FUNCTION aegis_vendor_master_guard()"
    )


def downgrade():
    op.execute("DROP TRIGGER trg_vendor_master_guard ON vendor_master")
    op.execute("DROP FUNCTION aegis_vendor_master_guard()")
    op.execute(
        "REVOKE UPDATE(paypal_payer_id,verified_paypal_email,trust_status,version,"
        "payee_last_changed_at) ON vendor_master FROM aegis_settlement_role"
    )
    op.execute("GRANT UPDATE ON vendor_master TO aegis_settlement_role")
