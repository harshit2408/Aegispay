"""Permit transactional settlement audit events without audit update privileges (R21/R22)."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("GRANT INSERT ON audit_queue TO aegis_settlement_role")
    op.execute("GRANT USAGE ON SEQUENCE audit_queue_queue_seq_seq TO aegis_settlement_role")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON audit_queue FROM aegis_settlement_role")
    op.execute("REVOKE USAGE ON SEQUENCE audit_queue_queue_seq_seq FROM aegis_settlement_role")
