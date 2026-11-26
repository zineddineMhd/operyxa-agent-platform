"""Run and platform metrics (F-11), computed from the business tables with plain SQL."""

from typing import Any

from sqlalchemy import Select, func, select

from operyxa.agent.store import RunStore
from operyxa.db.models import Approval, Finding, LLMUsage, Run, ToolCall, aware
from operyxa.db.session import Database


def _seconds(run: Run) -> float | None:
    if run.started_at and run.completed_at:
        return round((aware(run.completed_at) - aware(run.started_at)).total_seconds(), 1)
    return None


async def _breakdowns(db: Database, runs: Select) -> dict[str, Any]:
    async with db.session() as s:
        findings = (
            await s.execute(
                select(Finding.status, Finding.severity, func.count(Finding.id))
                .where(Finding.run_id.in_(runs))
                .group_by(Finding.status, Finding.severity)
            )
        ).all()
        tools = (
            await s.execute(
                select(
                    ToolCall.tool,
                    ToolCall.status,
                    func.count(ToolCall.id),
                    func.coalesce(func.avg(ToolCall.duration_ms), 0),
                )
                .where(ToolCall.run_id.in_(runs))
                .group_by(ToolCall.tool, ToolCall.status)
            )
        ).all()
        approvals = dict(
            (
                await s.execute(
                    select(Approval.status, func.count(Approval.id))
                    .where(Approval.run_id.in_(runs))
                    .group_by(Approval.status)
                )
            ).all()
        )
    by_status: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for status, severity, n in findings:
        by_status[status] = by_status.get(status, 0) + n
        if status == "verified":
            by_severity[severity] = by_severity.get(severity, 0) + n
    checked = by_status.get("verified", 0) + by_status.get("unverified", 0)
    per_tool: dict[str, dict[str, Any]] = {}
    for tool, status, n, avg_ms in tools:
        entry = per_tool.setdefault(
            tool, {"tool": tool, "calls": 0, "failed": 0, "rejected": 0, "avg_ms": 0.0}
        )
        if status == "completed":
            entry["avg_ms"] = round(float(avg_ms), 1)
        entry["calls"] += n
        if status in ("failed", "rejected"):
            entry[status] += n
    total_calls = sum(e["calls"] for e in per_tool.values())
    failures = sum(e["failed"] for e in per_tool.values())
    return {
        "findings_by_status": by_status,
        "verified_by_severity": by_severity,
        # Share of the model's claims that the deterministic verifier could confirm.
        "verification_rate": by_status.get("verified", 0) / checked if checked else None,
        "duplicates_merged": by_status.get("duplicate", 0),
        "tools": sorted(per_tool.values(), key=lambda e: -e["calls"]),
        "tool_error_rate": failures / total_calls if total_calls else None,
        "tool_rejections": sum(e["rejected"] for e in per_tool.values()),
        "approvals_by_status": approvals,
    }


async def run_metrics(db: Database, run: Run) -> dict[str, Any]:
    counters = await RunStore(db).counters(run.id)
    breakdown = await _breakdowns(db, select(Run.id).where(Run.id == run.id))
    limits = run.limits or {}
    async with db.session() as s:
        models = dict(
            (
                await s.execute(
                    select(LLMUsage.model, func.count(LLMUsage.id))
                    .where(LLMUsage.run_id == run.id)
                    .group_by(LLMUsage.model)
                )
            ).all()
        )
    return {
        "run_id": str(run.id),
        "status": run.status,
        "stop_reason": run.stop_reason,
        "duration_seconds": _seconds(run),
        "usage": counters,
        # Model calls per model: shows when the backup model or provider answered.
        "models": {name: int(n) for name, n in models.items()},
        "budget_used": {
            k: (counters[c] / limits[k] if limits.get(k) else None)
            for k, c in (
                ("max_llm_calls", "llm_calls"),
                ("max_tool_calls", "tool_calls"),
                ("max_cost_usd", "cost_usd"),
            )
        },
        **breakdown,
    }


async def platform_metrics(db: Database, runs: Select) -> dict[str, Any]:
    async with db.session() as s:
        rows = (
            await s.execute(
                select(Run.started_at, Run.completed_at, Run.stop_reason, Run.workflow_type).where(
                    Run.id.in_(runs)
                )
            )
        ).all()
    durations = [
        (aware(c) - aware(a)).total_seconds()
        for a, c, _, _ in rows
        if a is not None and c is not None
    ]
    stops: dict[str, int] = {}
    workflows: dict[str, int] = {}
    for _, _, stop, workflow in rows:
        if stop:
            stops[stop] = stops.get(stop, 0) + 1
        workflows[workflow] = workflows.get(workflow, 0) + 1
    return {
        "avg_duration_seconds": round(sum(durations) / len(durations), 1) if durations else None,
        "stop_reasons": stops,
        "runs_by_workflow": workflows,
        **await _breakdowns(db, runs),
    }
