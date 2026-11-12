"""P2-13: settlement-reported service health for the public /api/health probe.

The API process holds no Treasury credential and no Treasury account email (R22), so it cannot
probe the Treasury token, webhook registration or account distinctness (R12) itself. Settlement
writes booleans here; the API only reads them. No secret, identifier or email is stored.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "service_health",
        sa.Column("service", sa.Text(), primary_key=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("service = 'settlement'", name="ck_service_health_service"),
    )
    op.execute("GRANT SELECT ON service_health TO aegis_api_role")
    op.execute("GRANT SELECT, INSERT, UPDATE ON service_health TO aegis_settlement_role")


def downgrade():
    op.drop_table("service_health")
