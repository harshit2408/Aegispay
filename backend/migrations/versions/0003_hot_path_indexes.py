"""indexes for the settlement hot paths (outbox claim, reconciler scan, open reserve, webhooks)

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_spend_execution", "spend_ledger", ["execution_id"])
    op.create_index("ix_exec_status", "invoice_executions", ["status"])
    op.create_index("ix_exec_paypal_invoice", "invoice_executions", ["paypal_invoice_id"])
    op.create_index(
        "ix_webhook_unprocessed",
        "webhook_events",
        ["received_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )
    op.create_index(
        "ix_outbox_ready",
        "settlement_outbox",
        ["created_at"],
        postgresql_where=sa.text("status IN ('PENDING','CLAIMED')"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_ready", "settlement_outbox")
    op.drop_index("ix_webhook_unprocessed", "webhook_events")
    op.drop_index("ix_exec_paypal_invoice", "invoice_executions")
    op.drop_index("ix_exec_status", "invoice_executions")
    op.drop_index("ix_spend_execution", "spend_ledger")
