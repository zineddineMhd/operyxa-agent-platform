"""Business tables. LangGraph checkpoints live in their own tables, managed by the checkpointer."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")

RUN_STATUSES = ("pending", "running", "waiting_approval", "completed", "failed", "cancelled")
TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="developer")
    created_at: Mapped[datetime] = _created()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    repo_owner: Mapped[str] = mapped_column(String(100))
    repo_name: Mapped[str] = mapped_column(String(100))
    # Fernet-encrypted read-only token for a private repository. Never returned by the API.
    github_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Separate token with write access (Contents + Pull requests), used only when a person
    # confirms a pull request (F-12). Agents never receive it.
    github_write_token_encrypted: Mapped[str | None] = mapped_column(Text)
    # Shared with a team (F-13): its members get access according to their team role.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        Index("idx_projects_owner_created", "owner_id", "created_at"),
        Index("idx_projects_team", "team_id"),
    )


TEAM_ROLES = ("owner", "admin", "member", "viewer")


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _created()


class TeamMember(Base):
    __tablename__ = "team_members"
    team_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        Index("idx_team_members_user", "user_id"),
        CheckConstraint(
            "role IN ('owner','admin','member','viewer')", name="team_members_role_check"
        ),
    )


class ToolPolicy(Base):
    """Per-project override of the catalog policy (F-10). It can only tighten it."""

    __tablename__ = "tool_policies"
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String(128))
    mode: Mapped[str] = mapped_column(String(32))  # default | approval_required | disabled
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("project_id", "tool", name="uq_tool_policies_tool"),
        CheckConstraint(
            "mode IN ('default','approval_required','disabled')", name="tool_policies_mode_check"
        ),
    )


class Workflow(Base):
    """A reusable mission (F-13): which agents, which objective, which options."""

    __tablename__ = "workflows"
    id: Mapped[uuid.UUID] = _pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    base_type: Mapped[str] = mapped_column(String(64))
    objective_template: Mapped[str] = mapped_column(Text)
    agents: Mapped[list[str]] = mapped_column(Json, default=list)
    options: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("idx_workflows_owner", "owner_id"),)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(String(255), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    objective: Mapped[str] = mapped_column(Text)
    workflow_type: Mapped[str] = mapped_column(String(64), default="repository_analysis")
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    options: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    limits: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    # Effective tool policy overrides, frozen at creation like the limits: {tool: mode}.
    policy: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    stop_reason: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # The analysed commit is pinned when the run starts, so evidence stays reproducible.
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    plan: Mapped[dict[str, Any] | None] = mapped_column(Json)
    locked_by: Mapped[str | None] = mapped_column(String(100))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("idx_runs_project_created", "project_id", "created_at"),
        Index("idx_runs_status_created", "status", "created_at"),
        UniqueConstraint("created_by", "idempotency_key", name="uq_runs_idempotency"),
        CheckConstraint(
            "status IN ('pending','running','waiting_approval','completed','failed','cancelled')",
            name="runs_status_check",
        ),
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer, default=0)
    agent: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list[str]] = mapped_column(Json, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("run_id", "step_key", name="uq_run_steps_key"),)


class RunEvent(Base):
    """Append-only event log. `id` is the SSE cursor (Last-Event-ID)."""

    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("idx_run_events_run_id", "run_id", "id"),)


class ToolCall(Base):
    """One gateway decision + execution. A completed call is also a piece of evidence."""

    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    evidence_id: Mapped[str] = mapped_column(String(32))
    step_key: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(128))
    args: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    risk: Mapped[str] = mapped_column(String(16))
    decision: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # completed | failed | rejected
    error_code: Mapped[str | None] = mapped_column(String(64))
    result_text: Mapped[str | None] = mapped_column(Text)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        Index("idx_tool_calls_run_created", "run_id", "created_at"),
        UniqueConstraint("run_id", "evidence_id", name="uq_tool_calls_evidence"),
    )


class Approval(Base):
    """A human decision request, bound to the exact arguments of one tool call."""

    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(128))
    args: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    # SHA-256 of (step, tool, arguments): an approval never covers a different call.
    args_hash: Mapped[str] = mapped_column(String(64))
    risk: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("run_id", "args_hash", name="uq_approvals_call"),
        Index("idx_approvals_status", "status", "requested_at"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','expired')", name="approvals_status_check"
        ),
    )


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    recommendation: Mapped[str] = mapped_column(Text, default="")
    # draft -> verified | unverified | duplicate. Only deterministic code sets this.
    status: Mapped[str] = mapped_column(String(16), default="draft")
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    # Set when status is "duplicate": the finding this one was merged into.
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("idx_findings_run", "run_id", "created_at"),)


class Report(Base):
    __tablename__ = "reports"
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    partial: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text)
    body: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    step_key: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("idx_llm_usage_run", "run_id"),)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("idx_audit_resource", "resource_type", "resource_id"),)


PR_STATUSES = ("awaiting_confirmation", "creating", "created", "failed", "cancelled")


class PullRequestProposal(Base):
    """A pull request prepared from a run, created on GitHub only after a person confirms it.

    `content_hash` covers the exact files, branch and title: the confirmation must quote it, so
    a person can only confirm what was shown to them (F-12).
    """

    __tablename__ = "pull_request_proposals"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_confirmation")
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    branch: Mapped[str] = mapped_column(String(200))
    base_sha: Mapped[str] = mapped_column(String(64))
    files: Mapped[list[dict[str, Any]]] = mapped_column(Json, default=list)
    diff: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        Index("idx_pr_proposals_run", "run_id", "created_at"),
        CheckConstraint(
            "status IN ('awaiting_confirmation','creating','created','failed','cancelled')",
            name="pr_proposals_status_check",
        ),
    )
