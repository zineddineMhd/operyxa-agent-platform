"""teams, tool policies, workflows, pull request proposals (F-10, F-12, F-13)

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "team_members",
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="team_members_role_check"
        ),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("team_id", "user_id"),
    )
    op.create_index("idx_team_members_user", "team_members", ["user_id"])

    op.add_column("projects", sa.Column("github_write_token_encrypted", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("team_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "projects_team_id_fkey", "projects", "teams", ["team_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("idx_projects_team", "projects", ["team_id"])

    op.add_column("runs", sa.Column("workflow_id", sa.Uuid(), nullable=True))
    op.add_column("runs", sa.Column("policy", JSON, nullable=False, server_default=sa.text("'{}'")))

    op.create_table(
        "tool_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('default','approval_required','disabled')", name="tool_policies_mode_check"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "tool", name="uq_tool_policies_tool"),
    )
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("base_type", sa.String(length=64), nullable=False),
        sa.Column("objective_template", sa.Text(), nullable=False),
        sa.Column("agents", JSON, nullable=False),
        sa.Column("options", JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workflows_owner", "workflows", ["owner_id"])
    op.create_table(
        "pull_request_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("branch", sa.String(length=200), nullable=False),
        sa.Column("base_sha", sa.String(length=64), nullable=False),
        sa.Column("files", JSON, nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_url", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('awaiting_confirmation','creating','created','failed','cancelled')",
            name="pr_proposals_status_check",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_pr_proposals_run", "pull_request_proposals", ["run_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_pr_proposals_run", table_name="pull_request_proposals")
    op.drop_table("pull_request_proposals")
    op.drop_index("idx_workflows_owner", table_name="workflows")
    op.drop_table("workflows")
    op.drop_table("tool_policies")
    op.drop_column("runs", "policy")
    op.drop_column("runs", "workflow_id")
    op.drop_index("idx_projects_team", table_name="projects")
    op.drop_constraint("projects_team_id_fkey", "projects", type_="foreignkey")
    op.drop_column("projects", "team_id")
    op.drop_column("projects", "github_write_token_encrypted")
    op.drop_index("idx_team_members_user", table_name="team_members")
    op.drop_table("team_members")
    op.drop_table("teams")
