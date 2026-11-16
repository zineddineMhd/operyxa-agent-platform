import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from operyxa.agent.store import RunStore
from operyxa.api.deps import AppSettings, CurrentUser, Db, OwnedRun, Paging, owned_project
from operyxa.api.schemas import (
    EventOut,
    FindingOut,
    Page,
    ReportOut,
    RunAccepted,
    RunDetail,
    RunIn,
    RunOut,
    StepOut,
    ToolCallDetail,
    ToolCallOut,
)
from operyxa.db.models import (
    TERMINAL_STATUSES,
    Finding,
    Project,
    Report,
    Run,
    RunEvent,
    ToolCall,
    utcnow,
)
from operyxa.db.session import Database
from operyxa.errors import Conflict, NotFound

router = APIRouter(tags=["runs"])
SSE_POLL_SECONDS = 0.5
SSE_KEEPALIVE_SECONDS = 15.0


def _accepted(run: Run) -> RunAccepted:
    return RunAccepted(run_id=run.id, status=run.status, events_url=f"/api/v1/runs/{run.id}/events")


@router.post("/runs", response_model=RunAccepted, status_code=202)
async def start_run(
    body: RunIn,
    db: Db,
    user: CurrentUser,
    settings: AppSettings,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> RunAccepted:
    project = await owned_project(db, user, body.project_id)
    run = Run(
        project_id=project.id,
        created_by=user.id,
        thread_id=f"run-{uuid.uuid4()}",
        idempotency_key=idempotency_key,
        objective=body.objective,
        workflow_type=body.workflow_type,
        options=body.options.model_dump(),
        limits={
            "max_llm_calls": settings.max_agent_steps,
            "max_tool_calls": settings.max_tool_calls,
            "max_seconds": settings.max_run_seconds,
            "max_cost_usd": settings.max_run_cost_usd,
        },
    )
    try:
        async with db.session.begin() as s:
            s.add(run)
            await s.flush()
            s.add(RunEvent(run_id=run.id, type="run.created", data={"objective": run.objective}))
    except IntegrityError:
        # Same Idempotency-Key replayed: return the run created the first time.
        async with db.session() as s:
            existing = (
                await s.execute(
                    select(Run).where(
                        Run.created_by == user.id, Run.idempotency_key == idempotency_key
                    )
                )
            ).scalar_one()
        return _accepted(existing)
    await RunStore(db).audit(user.id, "run.created", "run", str(run.id))
    return _accepted(run)


@router.get("/runs", response_model=Page[RunOut])
async def list_runs(
    db: Db,
    user: CurrentUser,
    paging: Paging = Depends(),
    status: Literal["pending", "running", "waiting_approval", "completed", "failed", "cancelled"]
    | None = None,
    project_id: uuid.UUID | None = None,
) -> dict:
    base = (
        select(Run).join(Project, Project.id == Run.project_id).where(Project.owner_id == user.id)
    )
    if status:
        base = base.where(Run.status == status)
    if project_id:
        base = base.where(Run.project_id == project_id)
    async with db.session() as s:
        total = await s.scalar(select(func.count()).select_from(base.subquery()))
        items = (
            await s.execute(
                base.order_by(Run.created_at.desc(), Run.id)
                .limit(paging.limit)
                .offset(paging.offset)
            )
        ).scalars()
        return {
            "items": list(items),
            "total": total,
            "limit": paging.limit,
            "offset": paging.offset,
        }


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run: OwnedRun, db: Db) -> dict:
    usage = await RunStore(db).counters(run.id)
    return {**RunOut.model_validate(run).model_dump(), "usage": usage}


@router.get("/runs/{run_id}/steps", response_model=list[StepOut])
async def get_steps(run: OwnedRun, db: Db) -> list:
    return await RunStore(db).list_steps(run.id)


@router.post("/runs/{run_id}/cancel", response_model=RunOut)
async def cancel_run(run: OwnedRun, db: Db, user: CurrentUser) -> Run:
    store = RunStore(db)
    async with db.session.begin() as s:
        # Atomic for a queued run: either we cancel it or a worker has already claimed it.
        result = await s.execute(
            update(Run)
            .where(Run.id == run.id, Run.status == "pending")
            .values(
                status="cancelled",
                stop_reason="cancelled",
                cancel_requested_at=utcnow(),
                completed_at=utcnow(),
            )
        )
        if result.rowcount:
            s.add(RunEvent(run_id=run.id, type="run.cancelled", data={"stop_reason": "cancelled"}))
        else:
            result = await s.execute(
                update(Run)
                .where(Run.id == run.id, Run.status.in_(("running", "waiting_approval")))
                .values(cancel_requested_at=utcnow())
            )
            if not result.rowcount:
                raise Conflict("this run is already finished", code="RUN_NOT_CANCELLABLE")
    await store.audit(user.id, "run.cancel_requested", "run", str(run.id))
    return await store.get_run(run.id)


@router.get("/runs/{run_id}/tool-calls", response_model=Page[ToolCallOut])
async def list_tool_calls(run: OwnedRun, db: Db, paging: Paging = Depends()) -> dict:
    where = ToolCall.run_id == run.id
    async with db.session() as s:
        total = await s.scalar(select(func.count(ToolCall.id)).where(where))
        items = (
            await s.execute(
                select(ToolCall)
                .where(where)
                .order_by(ToolCall.created_at, ToolCall.id)
                .limit(paging.limit)
                .offset(paging.offset)
            )
        ).scalars()
        return {
            "items": list(items),
            "total": total,
            "limit": paging.limit,
            "offset": paging.offset,
        }


@router.get("/runs/{run_id}/evidence/{evidence_id}", response_model=ToolCallDetail)
async def get_evidence(run: OwnedRun, evidence_id: str, db: Db) -> ToolCall:
    """The (redacted) tool output a finding cites: one click from a claim to its proof."""
    async with db.session() as s:
        call = (
            await s.execute(
                select(ToolCall).where(
                    ToolCall.run_id == run.id, ToolCall.evidence_id == evidence_id
                )
            )
        ).scalar_one_or_none()
    if call is None:
        raise NotFound("evidence not found")
    return call


@router.get("/runs/{run_id}/findings", response_model=list[FindingOut])
async def list_findings(
    run: OwnedRun, db: Db, status: Literal["verified", "unverified", "draft"] | None = None
) -> list:
    query = select(Finding).where(Finding.run_id == run.id)
    if status:
        query = query.where(Finding.status == status)
    async with db.session() as s:
        return list((await s.execute(query.order_by(Finding.created_at, Finding.id))).scalars())


async def _report(db: Database, run: Run) -> Report:
    async with db.session() as s:
        report = (
            await s.execute(select(Report).where(Report.run_id == run.id))
        ).scalar_one_or_none()
    if report is None:
        raise NotFound("no report yet for this run", code="REPORT_NOT_READY")
    return report


@router.get("/runs/{run_id}/report", response_model=ReportOut)
async def get_report(run: OwnedRun, db: Db) -> Report:
    return await _report(db, run)


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
async def get_report_markdown(run: OwnedRun, db: Db) -> PlainTextResponse:
    report = await _report(db, run)
    return PlainTextResponse(report.markdown, media_type="text/markdown; charset=utf-8")


async def _events_after(
    db: Database, run_id: uuid.UUID, cursor: int, limit: int = 200
) -> list[RunEvent]:
    async with db.session() as s:
        return list(
            (
                await s.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.id > cursor)
                    .order_by(RunEvent.id)
                    .limit(limit)
                )
            ).scalars()
        )


@router.get("/runs/{run_id}/events/history", response_model=list[EventOut])
async def events_history(
    run: OwnedRun,
    db: Db,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list:
    return await _events_after(db, run.id, after, limit)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run: OwnedRun,
    db: Db,
    request: Request,
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Server-Sent Events. Events are persisted, so a client reconnects with Last-Event-ID
    (or ?after=) and receives everything it missed, in order."""
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after
    run_id = run.id

    async def stream() -> AsyncIterator[str]:
        nonlocal cursor
        idle = 0.0
        while not await request.is_disconnected():
            # Read the status *before* the events: once it is terminal, no event can follow.
            async with db.session() as s:
                status = await s.scalar(select(Run.status).where(Run.id == run_id))
            events = await _events_after(db, run_id, cursor)
            for event in events:
                cursor = event.id
                yield f"id: {event.id}\nevent: {event.type}\ndata: {json.dumps(event.data)}\n\n"
            if events:
                idle = 0.0
                continue
            if status in TERMINAL_STATUSES:
                yield f"event: stream.end\ndata: {json.dumps({'status': status})}\n\n"
                return
            await asyncio.sleep(SSE_POLL_SECONDS)
            idle += SSE_POLL_SECONDS
            if idle >= SSE_KEEPALIVE_SECONDS:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
