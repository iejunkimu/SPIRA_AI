"""Create the cases table.

One table for two kinds of rows (KB-05, KB-06, server spec appendix B):
- synthetic: loaded by the knowledge base pipeline (SPIRA_KB spira/steps/embed_load.py)
- this_home: written by the server (S7), read by S4

The first ten columns must match SPIRA_KB embed_load.py COLUMNS exactly.
`executed_at` is not a column; it lives in `when` for this_home rows.

Revision ID: 0001
Revises:
Create Date: 2026-11-14
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE cases (
            -- SPIRA_KB embed_load.py COLUMNS (KB-05)
            case_id       text PRIMARY KEY,
            source        text NOT NULL
                          CHECK (source IN ('synthetic', 'this_home', 'other_home')),
            items         text[] NOT NULL,
            intent_id     text NOT NULL,
            intent_text   text,
            situation     jsonb NOT NULL DEFAULT '{}'::jsonb,
            "when"        jsonb NOT NULL DEFAULT '{}'::jsonb,
            household     jsonb NOT NULL DEFAULT '{}'::jsonb,
            basket_emb    vector(1024) NOT NULL,
            kb_version    text,

            -- this_home only (written by the server); NULL for synthetic rows
            household_id  text,
            home_did      jsonb,
            verdict       text,
            task_feedback jsonb,

            created_at    timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT this_home_has_household
                CHECK (source <> 'this_home' OR household_id IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX cases_basket_emb_hnsw ON cases "
        "USING hnsw (basket_emb vector_cosine_ops)"
    )
    op.execute("CREATE INDEX cases_source_household ON cases (source, household_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS cases")
    # The vector extension is left in place; other tables may use it.
