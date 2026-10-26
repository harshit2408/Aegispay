"""write-ahead marker for the vendor-side record-payment call (PRD §9.2, R7)

payouts.recordpay_sent_at is committed before POST /v2/invoicing/invoices/{id}/payments, so a
crash between that call and SETTLED is recognised as this execution's own attempt instead of
being inferred from the invoice's aggregate paid amount.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payouts", sa.Column("recordpay_sent_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("payouts", "recordpay_sent_at")
