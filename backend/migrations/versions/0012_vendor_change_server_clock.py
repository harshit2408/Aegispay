"""R15/R22: vendor-change timestamps come from the database clock, not the writer.

0006 checks approved_at/effective_at against created_at, but all three were writable by
aegis_api_role, so a compromised API could insert an already-cooled COOLING row and have
settlement apply a new payee immediately. Non-superuser writers now get server-set times.
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION aegis_vendor_change_clock() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
        BEGIN
            IF (SELECT rolsuper FROM pg_roles WHERE rolname=current_user) THEN
                RETURN NEW;
            END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'REQUESTED' THEN
                    RAISE EXCEPTION 'vendor changes must start as REQUESTED (R15)'
                        USING ERRCODE='integrity_constraint_violation';
                END IF;
                NEW.created_at := now();
            ELSIF OLD.status='REQUESTED' AND NEW.status='COOLING' THEN
                NEW.approved_at := now();
                NEW.effective_at := GREATEST(NEW.effective_at, now()+interval '72 hours');
            END IF;
            RETURN NEW;
        END $$
    """)
    # Fires before trg_vendor_change_guard (triggers run in name order), so 0006 validates
    # the server-set values.
    op.execute(
        "CREATE TRIGGER trg_vendor_change_clock BEFORE INSERT OR UPDATE ON vendor_master_changes "
        "FOR EACH ROW EXECUTE FUNCTION aegis_vendor_change_clock()"
    )


def downgrade():
    op.execute("DROP TRIGGER trg_vendor_change_clock ON vendor_master_changes")
    op.execute("DROP FUNCTION aegis_vendor_change_clock()")
