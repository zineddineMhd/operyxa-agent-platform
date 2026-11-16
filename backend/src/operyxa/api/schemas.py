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


class ProjectOut(Out):
    id: uuid.UUID
    name: str
    repo_owner: str
    repo_name: str
    created_at: datetime
    github_token_encrypted: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def has_token(self) -> bool:
        return self.github_token_encrypted is not None


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read_only: Literal[True] = True  # the MVP has no write path at all
    run_tests: Literal[False] = False  # sandboxed test execution arrives in V1


class RunIn(BaseModel):
    project_id: uuid.UUID
    objective: str = Field(min_length=10, max_length=4000)
    workflow_type: Literal["repository_analysis"] = "repository_analysis"
    options: RunOptions = RunOptions()


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: str
    events_url: str


class RunOut(Out):
    id: uuid.UUID
    project_id: uuid.UUID
    objective: str
    workflow_type: str
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
