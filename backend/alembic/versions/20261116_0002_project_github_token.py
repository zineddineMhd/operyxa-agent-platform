"""project github token

Only adds the encrypted GitHub token column. The LangGraph checkpoint tables are
owned by the checkpointer (AsyncPostgresSaver.setup()) and must never be touched here.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("github_token_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "github_token_encrypted")
