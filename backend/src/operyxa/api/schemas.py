import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    computed_field,
    field_validator,
)

_REPO_URL = re.compile(
    r"^(?:https://github\.com/)?([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100}?)(?:\.git)?/?$"
)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class UserOut(Out):
    id: uuid.UUID
    email: str
    role: str
    created_at: datetime


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository_url: str = Field(description="https://github.com/owner/repo or owner/repo")
    github_token: SecretStr | None = Field(
        default=None,
        min_length=20,
        max_length=400,
        description="Read-only token for a private repository. Stored encrypted, never returned.",
    )
    github_write_token: SecretStr | None = Field(
        default=None,
        min_length=20,
        max_length=400,
        description="Token allowed to create branches and pull requests (Contents + Pull "
        "requests: write). Used only when a person confirms a pull request. Never returned.",
    )
    team_id: uuid.UUID | None = None

    @field_validator("repository_url")
    @classmethod
    def _github_only(cls, value: str) -> str:
        if not _REPO_URL.match(value.strip()):
            raise ValueError("must be a GitHub repository: https://github.com/owner/repo")
        return value.strip()

    def repo(self) -> tuple[str, str]:
        match = _REPO_URL.match(self.repository_url)
        assert match
        return match.group(1), match.group(2)


class ProjectUpdate(BaseModel):
    """Only the fields sent are changed. An empty string removes a token."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    github_token: SecretStr | None = Field(default=None, max_length=400)
    github_write_token: SecretStr | None = Field(default=None, max_length=400)
    team_id: uuid.UUID | None = None
    clear_team: bool = False


class ProjectOut(Out):
    id: uuid.UUID
    name: str
    repo_owner: str
    repo_name: str
    team_id: uuid.UUID | None = None
    role: str | None = None
    created_at: datetime
    github_token_encrypted: str | None = Field(default=None, exclude=True)
    github_write_token_encrypted: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_token(self) -> bool:
        return self.github_token_encrypted is not None

    @computed_field
    @property
    def has_write_token(self) -> bool:
        return self.github_write_token_encrypted is not None


class ToolPolicyIn(BaseModel):
    tool: str = Field(max_length=128)
    mode: Literal["default", "approval_required", "disabled"]


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TeamMemberIn(BaseModel):
    email: EmailStr
    role: Literal["admin", "member", "viewer"] = "member"


class TeamMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    role: str


class TeamOut(Out):
    id: uuid.UUID
    name: str
    created_at: datetime
    role: str | None = None
    members: list[TeamMemberOut] = []


class WorkflowIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    base_type: str = Field(max_length=64)
    objective_template: str = Field(min_length=10, max_length=4000)
    agents: list[str] | None = Field(
        default=None, description="Subset of the base template's agents; default: all of them"
    )
    run_tests: bool = False
    propose_fixes: bool = False
    team_id: uuid.UUID | None = None


class WorkflowOut(Out):
    id: uuid.UUID
    name: str
    description: str
    base_type: str
    objective_template: str
    agents: list[str]
    options: dict[str, Any]
    team_id: uuid.UUID | None
    created_at: datetime


class PullRequestIn(BaseModel):
    title: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=10_000)
    evidence_ids: list[str] | None = Field(
        default=None, description="Fix proposals to include; default: all of the run's proposals"
    )
    include_report: bool = Field(
        default=False, description="Also add the Markdown report under docs/operyxa/"
    )


class PullRequestConfirm(BaseModel):
    content_hash: str = Field(
        min_length=64,
        max_length=64,
        description="Hash shown with the proposal: confirms exactly the reviewed content",
    )


class PullRequestOut(Out):
    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    title: str
    body: str
    branch: str
    base_sha: str
    files: list[dict[str, Any]]
    diff: str
    content_hash: str
    pr_number: int | None
    pr_url: str | None
    error: str | None
    created_at: datetime
    confirmed_at: datetime | None


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_only: Literal[True] = True  # the MVP has no write path at all
    # Runs the repository's test suite in a sandbox. Each execution waits for a human approval.
    run_tests: bool = False
    # Adds the remediation agent: it proposes fixes; a pull request needs a confirmation (F-12).
    propose_fixes: bool = False


class RunIn(BaseModel):
    project_id: uuid.UUID
    # Optional with a workflow: its objective template is used.
    objective: str | None = Field(default=None, min_length=10, max_length=4000)
    workflow_type: Literal[
        "repository_analysis", "security_audit", "architecture_review", "test_review", "remediation"
    ] = "repository_analysis"
    workflow_id: uuid.UUID | None = None
    options: RunOptions | None = None


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str
    events_url: str


class RunOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID
    objective: str
    workflow_type: str
    workflow_id: uuid.UUID | None = None
    options: dict[str, Any] = {}
    policy: dict[str, Any] = {}
    status: str
    stop_reason: str | None
    error: dict[str, Any] | None
    commit_sha: str | None
    plan: dict[str, Any] | None
    limits: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RunDetail(RunOut):
    usage: dict[str, int | float]


class StepOut(Out):
    step_key: str
    position: int
    agent: str
    action: str
    depends_on: list[str]
    status: str
    summary: str | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ToolCallOut(Out):
    id: uuid.UUID
    evidence_id: str
    step_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    risk: str
    decision: str
    status: str
    error_code: str | None
    truncated: bool
    attempts: int
    duration_ms: int
    created_at: datetime


class ToolCallDetail(ToolCallOut):
    result_text: str | None


class ApprovalOut(Out):
    id: uuid.UUID
    run_id: uuid.UUID
    step_key: str
    agent: str
    tool: str
    args: dict[str, Any]
    args_hash: str
    risk: str
    status: str
    reason: str | None
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class FindingOut(Out):
    id: uuid.UUID
    step_key: str
    agent: str
    title: str
    severity: str
    category: str
    description: str
    recommendation: str
    status: str
    duplicate_of: uuid.UUID | None
    evidence: list[dict[str, Any]]


class ReportOut(Out):
    run_id: uuid.UUID
    partial: bool
    summary: str
    body: dict[str, Any]
    markdown: str
    created_at: datetime


class EventOut(Out):
    id: int
    type: str
    data: dict[str, Any]
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
