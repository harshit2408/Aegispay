"""Durable invoice and authoring LLM admission across restarts (P2-10/R14)."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE llm_budgets (
        run_id TEXT PRIMARY KEY,
        requests INTEGER NOT NULL DEFAULT 0 CHECK (requests BETWEEN 0 AND 4),
        charged_micros BIGINT NOT NULL DEFAULT 0 CHECK (charged_micros BETWEEN 0 AND 1000000),
        halted BOOLEAN NOT NULL DEFAULT FALSE)""")
    op.execute("GRANT SELECT, INSERT, UPDATE ON llm_budgets TO aegis_api_role")


def downgrade():
    op.execute("DROP TABLE llm_budgets")
