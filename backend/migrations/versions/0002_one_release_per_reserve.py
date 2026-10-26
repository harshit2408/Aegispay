"""one release per reserve entry (R8, R17)

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_spend_one_release_per_reserve", "spend_ledger", ["reserve_entry_id"]
    )
    op.create_unique_constraint(
        "uq_poled_one_release_per_reserve", "po_ledger", ["reserve_entry_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_poled_one_release_per_reserve", "po_ledger", type_="unique")
    op.drop_constraint("uq_spend_one_release_per_reserve", "spend_ledger", type_="unique")
