"""Pull requests from fix proposals (F-12): prepare, review, confirm.

1. The remediation agent records proposals (`changes.propose_file_change`); nothing is written.
2. A person prepares a pull request from a finished run: the server snapshots the files, the
   branch and the diff, and returns a `content_hash` of exactly that content.
3. A person with the manage role confirms by quoting that hash. Only then does the server write
   to GitHub, with the project's separate write token: a new branch from the analysed commit,
   one commit, and a draft pull request. Every step is audited.
"""

import hashlib
import json
import uuid

from fastapi import APIRouter
from sqlalchemy import select, update

from operyxa.agent.store import RunStore
from operyxa.api.deps import (
    AppSettings,
    CurrentUser,
    Db,
    OwnedRun,
    accessible_projects,
    require,
    run_role,
)
from operyxa.api.schemas import PullRequestConfirm, PullRequestIn, PullRequestOut
from operyxa.crypto import SecretBox
from operyxa.db.models import Project, PullRequestProposal, Report, Run, RunEvent, ToolCall, utcnow
from operyxa.db.session import Database
from operyxa.errors import AppError, Conflict, NotFound
from operyxa.gateway.catalog import is_sensitive_path
from operyxa.gateway.redaction import MARKER, redact
from operyxa.mcp_servers.changes_server import unified_diff
from operyxa.mcp_servers.github_client import GitHubError
from operyxa.mcp_servers.github_writer import GitHubWriter

router = APIRouter(tags=["pull requests"])
PROPOSAL_TOOL = "changes.propose_file_change"


def writer_factory(settings, token: str) -> GitHubWriter:
    """Replaced in tests by a writer on a fake GitHub."""
    return GitHubWriter.create(settings.github_api_url, token)


async def _proposals(db: Database, run_id: uuid.UUID) -> list[dict]:
    async with db.session() as s:
        calls = (
            await s.execute(
                select(ToolCall)
                .where(
                    ToolCall.run_id == run_id,
                    ToolCall.tool == PROPOSAL_TOOL,
                    ToolCall.status == "completed",
                )
                .order_by(ToolCall.created_at, ToolCall.id)
            )
        ).scalars()
        out = []
        for call in calls:
            try:
                data = json.loads(call.result_text or "")
            except ValueError:
                continue
            if data.get("gateway_truncated") or not isinstance(data.get("new_content"), str):
                continue  # a cut proposal is never applied
            out.append({"evidence_id": call.evidence_id, "step": call.step_key, **data})
    # The last proposal for a path wins.
    latest: dict[str, dict] = {}
    for item in out:
        latest[item["path"]] = item
    return list(latest.values())


def _hash(title: str, branch: str, base_sha: str, files: list[dict]) -> str:
    payload = json.dumps([title, branch, base_sha, files], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


@router.get("/runs/{run_id}/changes")
async def list_changes(run: OwnedRun, db: Db) -> dict:
    """Fix proposals of a run, with their diffs (the full content stays in the evidence)."""
    return {
        "changes": [
            {k: v for k, v in p.items() if k != "new_content"} for p in await _proposals(db, run.id)
        ]
    }


@router.get("/runs/{run_id}/pull-requests", response_model=list[PullRequestOut])
async def list_pull_requests(run: OwnedRun, db: Db) -> list:
    async with db.session() as s:
        return list(
            (
                await s.execute(
                    select(PullRequestProposal)
                    .where(PullRequestProposal.run_id == run.id)
                    .order_by(PullRequestProposal.created_at)
                )
            ).scalars()
        )


@router.post("/runs/{run_id}/pull-requests", response_model=PullRequestOut, status_code=201)
async def prepare_pull_request(
    run: OwnedRun, body: PullRequestIn, db: Db, user: CurrentUser, settings: AppSettings
) -> PullRequestProposal:
    if not settings.pull_requests_enabled:
        raise AppError(
            "pull requests are disabled on this server",
            code="PULL_REQUESTS_DISABLED",
            status_code=422,
        )
    require(await run_role(db, user, run), "run")
    if run.status != "completed" or not run.commit_sha:
        raise Conflict("the run must be completed first", code="RUN_NOT_COMPLETED")
    proposals = await _proposals(db, run.id)
    if body.evidence_ids is not None:
        proposals = [p for p in proposals if p["evidence_id"] in set(body.evidence_ids)]
    files = [
        {
            "path": p["path"],
            "content": p["new_content"],
            "change_type": p["change_type"],
            "rationale": p.get("rationale", ""),
            "evidence_id": p["evidence_id"],
        }
        for p in proposals
    ]
    if body.include_report:
        async with db.session() as s:
            report = (
                await s.execute(select(Report).where(Report.run_id == run.id))
            ).scalar_one_or_none()
        if report is not None:
            files.append(
                {
                    "path": f"docs/operyxa/report-{str(run.id)[:8]}.md",
                    "content": report.markdown,
                    "change_type": "add",
                    "rationale": "Review report of the run",
                    "evidence_id": None,
                }
            )
    if not files:
        raise AppError(
            "this run has no fix proposal to include", code="NOTHING_TO_PROPOSE", status_code=422
        )
    for f in files:  # defence in depth: the same rules as the proposal tool
        if (
            is_sensitive_path(f["path"])
            or MARKER.search(f["content"])
            or redact(f["content"]) != f["content"]
        ):
            raise AppError(
                f"{f['path']} cannot be part of a pull request",
                code="FILE_REFUSED",
                status_code=422,
            )
    title = (body.title or f"Operyxa: {len(files)} proposed fix(es)")[:200]
    branch = f"operyxa/run-{str(run.id)[:8]}-{uuid.uuid4().hex[:6]}"
    lines = [body.body.strip()] if body.body.strip() else []
    lines += [
        f"Proposed by an Operyxa review of commit `{run.commit_sha}`.",
        "",
        *[f"- `{f['path']}`: {f['rationale']}" for f in files],
        "",
        "Generated by agents, confirmed by a person before creation. Review before merging.",
    ]
    diff = "\n".join(p.get("diff", "") for p in proposals) + "".join(
        unified_diff(f["path"], None, f["content"]) for f in files if f["evidence_id"] is None
    )
    proposal = PullRequestProposal(
        run_id=run.id,
        created_by=user.id,
        title=title,
        body="\n".join(lines),
        branch=branch,
        base_sha=run.commit_sha,
        files=files,
        diff=diff,
        content_hash=_hash(title, branch, run.commit_sha, files),
    )
    async with db.session.begin() as s:
        s.add(proposal)
        await s.flush()
        s.add(
            RunEvent(
                run_id=run.id,
                type="pull_request.prepared",
                data={"proposal_id": str(proposal.id), "files": len(files)},
            )
        )
    await RunStore(db).audit(
        user.id, "pull_request.prepared", "run", str(run.id), {"proposal_id": str(proposal.id)}
    )
    return proposal


async def _owned_proposal(
    db: Database, user, proposal_id: uuid.UUID
) -> tuple[PullRequestProposal, Run, Project]:
    async with db.session() as s:
        row = (
            await s.execute(
                select(PullRequestProposal, Run, Project)
                .join(Run, Run.id == PullRequestProposal.run_id)
                .join(Project, Project.id == Run.project_id)
                .where(
                    PullRequestProposal.id == proposal_id,
                    Run.project_id.in_(accessible_projects(user)),
                )
            )
        ).one_or_none()
    if row is None:
        raise NotFound("pull request proposal not found")
    return row  # type: ignore[return-value]


@router.post("/pull-requests/{proposal_id}/confirm", response_model=PullRequestOut)
async def confirm_pull_request(
    proposal_id: uuid.UUID,
    body: PullRequestConfirm,
    db: Db,
    user: CurrentUser,
    settings: AppSettings,
) -> PullRequestProposal:
    proposal, run, project = await _owned_proposal(db, user, proposal_id)
    require(await run_role(db, user, run), "manage")
    if (
        body.content_hash != proposal.content_hash
        or _hash(proposal.title, proposal.branch, proposal.base_sha, proposal.files)
        != proposal.content_hash
    ):
        raise Conflict(
            "the confirmation does not match the reviewed content", code="CONFIRMATION_MISMATCH"
        )
    if not project.github_write_token_encrypted:
        raise AppError(
            "add a write token to the project to create pull requests",
            code="WRITE_TOKEN_MISSING",
            status_code=422,
        )
    async with db.session.begin() as s:
        # Atomic: a double click, or two reviewers, create at most one pull request.
        claimed = await s.execute(
            update(PullRequestProposal)
            .where(
                PullRequestProposal.id == proposal_id,
                PullRequestProposal.status == "awaiting_confirmation",
            )
            .values(status="creating", confirmed_by=user.id, confirmed_at=utcnow())
        )
        if not claimed.rowcount:
            raise Conflict(
                "this proposal is no longer awaiting confirmation", code="PROPOSAL_NOT_PENDING"
            )
    store = RunStore(db)
    await store.audit(
        user.id,
        "pull_request.confirmed",
        "run",
        str(run.id),
        {"proposal_id": str(proposal_id), "content_hash": proposal.content_hash},
    )
    token = SecretBox(settings.secrets_key).decrypt(project.github_write_token_encrypted)
    values: dict
    try:
        result = await writer_factory(settings, token).create_pull_request(
            project.repo_owner,
            project.repo_name,
            base_sha=proposal.base_sha,
            branch=proposal.branch,
            title=proposal.title,
            body=proposal.body,
            files=[{"path": f["path"], "content": f["content"]} for f in proposal.files],
        )
    except GitHubError as exc:
        values = {"status": "failed", "error": f"{exc.code}: {exc.message}"[:500]}
    else:
        values = {"status": "created", "pr_number": result["number"], "pr_url": result["url"]}
    async with db.session.begin() as s:
        await s.execute(
            update(PullRequestProposal)
            .where(PullRequestProposal.id == proposal_id)
            .values(**values)
        )
        s.add(
            RunEvent(
                run_id=run.id,
                type=f"pull_request.{values['status']}",
                data={"proposal_id": str(proposal_id), "url": values.get("pr_url")},
            )
        )
    await store.audit(
        user.id,
        f"pull_request.{values['status']}",
        "run",
        str(run.id),
        {"proposal_id": str(proposal_id), **values},
    )
    async with db.session() as s:
        return (
            await s.execute(
                select(PullRequestProposal).where(PullRequestProposal.id == proposal_id)
            )
        ).scalar_one()


@router.post("/pull-requests/{proposal_id}/cancel", response_model=PullRequestOut)
async def cancel_pull_request(
    proposal_id: uuid.UUID, db: Db, user: CurrentUser
) -> PullRequestProposal:
    proposal, run, _ = await _owned_proposal(db, user, proposal_id)
    require(await run_role(db, user, run), "run")
    async with db.session.begin() as s:
        result = await s.execute(
            update(PullRequestProposal)
            .where(
                PullRequestProposal.id == proposal_id,
                PullRequestProposal.status == "awaiting_confirmation",
            )
            .values(status="cancelled")
        )
        if not result.rowcount:
            raise Conflict(
                "this proposal is no longer awaiting confirmation", code="PROPOSAL_NOT_PENDING"
            )
    await RunStore(db).audit(
        user.id, "pull_request.cancelled", "run", str(run.id), {"proposal_id": str(proposal_id)}
    )
    async with db.session() as s:
        return (
            await s.execute(
                select(PullRequestProposal).where(PullRequestProposal.id == proposal_id)
            )
        ).scalar_one()
