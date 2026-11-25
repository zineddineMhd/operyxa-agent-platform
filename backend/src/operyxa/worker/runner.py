"""Run execution: claim a run, execute or resume its graph, record the outcome.

The `runs` table is the queue (FOR UPDATE SKIP LOCKED + a heartbeat lease). A run whose worker
died is re-claimed after the lease expires and resumes from its last LangGraph checkpoint.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from operyxa.agent.budget import Limits, RunBudget
from operyxa.agent.graph import BASE_AGENTS, RunDeps, build_graph
from operyxa.agent.store import RunStore
from operyxa.config import Settings
from operyxa.crypto import SecretBox
from operyxa.db.models import Project, Run, aware, utcnow
from operyxa.db.session import Database
from operyxa.errors import BudgetExceeded, RunCancelled
from operyxa.gateway.gateway import CallContext, ToolGateway
from operyxa.llm.base import LLMClient, MeteredLLM

log = logging.getLogger(__name__)


@dataclass
class WorkerDeps:
    settings: Settings
    db: Database
    store: RunStore
    gateway: ToolGateway
    llm: LLMClient
    checkpointer: Any


async def claim_next_run(db: Database, worker_id: str, lease_seconds: int) -> uuid.UUID | None:
    stale = utcnow() - timedelta(seconds=lease_seconds)
    async with db.session.begin() as s:
        run = (
            await s.execute(
                select(Run)
                .where(
                    or_(
                        Run.status == "pending",
                        and_(Run.status == "running", Run.heartbeat_at < stale),
                    )
                )
                .order_by(Run.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if run is None:
            return None
        run.status, run.locked_by, run.heartbeat_at = "running", worker_id, utcnow()
        return run.id


async def _finish(
    deps: WorkerDeps,
    run_id: uuid.UUID,
    status: str,
    stop_reason: str | None,
    error: dict[str, Any] | None = None,
) -> None:
    await deps.store.update_run(
        run_id,
        status=status,
        stop_reason=stop_reason,
        error=error,
        completed_at=utcnow(),
        locked_by=None,
    )
    await deps.store.emit(run_id, f"run.{status}", {"stop_reason": stop_reason, "error": error})


async def execute_run(run_id: uuid.UUID, deps: WorkerDeps) -> None:
    store = deps.store
    run = await store.get_run(run_id)
    async with deps.db.session() as s:
        project = (
            await s.execute(select(Project).where(Project.id == run.project_id))
        ).scalar_one()
    if run.cancel_requested_at is not None:
        return await _finish(deps, run_id, "cancelled", "cancelled")
    started_at = aware(run.started_at) if run.started_at else utcnow()
    if run.started_at is None:
        await store.update_run(run_id, started_at=started_at)

    limits = Limits(**run.limits)
    budget = RunBudget(run_id, limits, store, started_at, await store.counters(run_id))
    scope = {"owner": project.repo_owner, "repo": project.repo_name, "ref": run.commit_sha or ""}
    try:
        if project.github_token_encrypted:
            scope["token"] = SecretBox(deps.settings.secrets_key).decrypt(
                project.github_token_encrypted
            )
        if not run.commit_sha:
            # Pin the analysed commit: every later read and every piece of evidence uses it.
            outcome = await deps.gateway.call(
                CallContext(run_id, "setup", "system", scope, budget, run.created_by),
                "github.get_repo",
                {},
            )
            if not outcome.ok:
                return await _finish(
                    deps,
                    run_id,
                    "failed",
                    "repository_unavailable",
                    {"code": outcome.error_code, "message": outcome.text},
                )
            scope["ref"] = json.loads(outcome.text)["head_sha"]
            await store.update_run(run_id, commit_sha=scope["ref"])
            await store.emit(run_id, "run.started", {"commit_sha": scope["ref"]})

        agents = set(BASE_AGENTS)
        if (run.options or {}).get("run_tests"):
            if not deps.gateway.tools_for("testing"):
                return await _finish(
                    deps,
                    run_id,
                    "failed",
                    "test_runner_unavailable",
                    {"code": "TEST_RUNNER_UNAVAILABLE", "message": "no test runner is connected"},
                )
            agents.add("testing")
        graph = build_graph(
            RunDeps(
                run_id=run_id,
                actor_id=run.created_by,
                objective=run.objective,
                scope=scope,
                settings=deps.settings,
                store=store,
                gateway=deps.gateway,
                budget=budget,
                llm=MeteredLLM(deps.llm, run_id, budget, store, deps.settings.llm_pricing),
                agents=frozenset(agents),
            ),
            deps.checkpointer,
        )
        config = {"configurable": {"thread_id": run.thread_id}, "recursion_limit": 60}
        snapshot = await graph.aget_state(config)
        if snapshot.values and not snapshot.next:
            final = snapshot.values  # the graph had finished; only the outcome was not recorded
        else:
            resume = bool(snapshot.next)
            if resume:
                await store.emit(run_id, "run.resumed", {"next": list(snapshot.next)})
            remaining = limits.max_seconds - (utcnow() - started_at).total_seconds()
            # Generous outer bound: budgets are enforced inside; approval waits are not run time.
            grace = 30 + 4 * deps.settings.approval_ttl_seconds
            async with asyncio.timeout(max(remaining, 0) + grace):
                final = await graph.ainvoke(
                    None if resume else {"step_results": {}, "plan_attempts": 0}, config
                )
    except TimeoutError:
        return await _finish(deps, run_id, "failed", "budget_exceeded:time")
    except (BudgetExceeded, RunCancelled) as exc:
        cancelled = isinstance(exc, RunCancelled)
        return await _finish(
            deps,
            run_id,
            "cancelled" if cancelled else "failed",
            "cancelled" if cancelled else f"budget_exceeded:{exc.kind}",
        )
    except Exception as exc:
        log.exception("run %s crashed", run_id)
        return await _finish(
            deps,
            run_id,
            "failed",
            "internal_error",
            {"code": "INTERNAL_ERROR", "message": type(exc).__name__},
        )

    stop = final.get("stop_reason")
    status = "completed" if stop is None else "cancelled" if stop == "cancelled" else "failed"
    await _finish(deps, run_id, status, stop)


async def _heartbeat(deps: WorkerDeps, run_id: uuid.UUID) -> None:
    while True:
        await asyncio.sleep(deps.settings.worker_lease_seconds / 3)
        await deps.store.update_run(run_id, heartbeat_at=utcnow())


async def run_with_heartbeat(run_id: uuid.UUID, deps: WorkerDeps) -> None:
    beat = asyncio.create_task(_heartbeat(deps, run_id))
    try:
        await execute_run(run_id, deps)
    finally:
        beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beat


async def worker_loop(deps: WorkerDeps, stop: asyncio.Event, worker_id: str) -> None:
    slots = asyncio.Semaphore(deps.settings.worker_concurrency)
    tasks: set[asyncio.Task] = set()
    while not stop.is_set():
        await slots.acquire()
        run_id = await claim_next_run(deps.db, worker_id, deps.settings.worker_lease_seconds)
        if run_id is None:
            slots.release()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), deps.settings.worker_poll_seconds)
            continue
        task = asyncio.create_task(run_with_heartbeat(run_id, deps))
        tasks.add(task)
        task.add_done_callback(lambda t: (tasks.discard(t), slots.release()))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
