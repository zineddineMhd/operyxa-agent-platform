"""Business projection of a run in PostgreSQL: what the API and the UI read.

The UI never depends on LangGraph's internal checkpoint format; it reads these tables.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from operyxa.db.models import (
    Approval,
    AuditEvent,
    Finding,
    LLMUsage,
    Report,
    Run,
    RunEvent,
    RunStep,
    ToolCall,
    utcnow,
)
from operyxa.db.session import Database


class RunStore:
    def __init__(self, db: Database):
        self._db = db

    async def emit(self, run_id: uuid.UUID, type_: str, data: dict[str, Any] | None = None) -> None:
        async with self._db.session.begin() as s:
            s.add(RunEvent(run_id=run_id, type=type_, data=data or {}))

    async def audit(
        self,
        actor_id: uuid.UUID | None,
        action: str,
        resource_type: str,
        resource_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        async with self._db.session.begin() as s:
            s.add(
                AuditEvent(
                    actor_id=actor_id,
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    data=data or {},
                )
            )

    async def get_run(self, run_id: uuid.UUID) -> Run:
        async with self._db.session() as s:
            return (await s.execute(select(Run).where(Run.id == run_id))).scalar_one()

    async def update_run(self, run_id: uuid.UUID, **fields: Any) -> None:
        async with self._db.session.begin() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(**fields))

    async def cancel_requested(self, run_id: uuid.UUID) -> bool:
        async with self._db.session() as s:
            value = await s.scalar(select(Run.cancel_requested_at).where(Run.id == run_id))
        return value is not None

    # -- plan and steps ---------------------------------------------------------------
    async def save_plan(self, run_id: uuid.UUID, plan: dict[str, Any]) -> None:
        async with self._db.session.begin() as s:
            await s.execute(update(Run).where(Run.id == run_id).values(plan=plan))
            await s.execute(delete(RunStep).where(RunStep.run_id == run_id))
            for position, step in enumerate(plan["steps"]):
                s.add(
                    RunStep(
                        run_id=run_id,
                        step_key=step["id"],
                        position=position,
                        agent=step["agent"],
                        action=step["action"],
                        depends_on=step.get("depends_on", []),
                    )
                )

    async def update_step(self, run_id: uuid.UUID, step_key: str, **fields: Any) -> None:
        async with self._db.session.begin() as s:
            await s.execute(
                update(RunStep)
                .where(RunStep.run_id == run_id, RunStep.step_key == step_key)
                .values(**fields)
            )

    # -- tool calls / evidence --------------------------------------------------------
    async def find_completed_call(self, run_id: uuid.UUID, key: str) -> ToolCall | None:
        async with self._db.session() as s:
            return (
                await s.execute(
                    select(ToolCall)
                    .where(
                        ToolCall.run_id == run_id,
                        ToolCall.idempotency_key == key,
                        ToolCall.status == "completed",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def save_tool_call(self, **fields: Any) -> ToolCall:
        call = ToolCall(**fields)
        async with self._db.session.begin() as s:
            s.add(call)
        return call

    async def evidence_map(self, run_id: uuid.UUID) -> dict[str, ToolCall]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(ToolCall).where(
                        ToolCall.run_id == run_id, ToolCall.status == "completed"
                    )
                )
            ).scalars()
            return {row.evidence_id: row for row in rows}

    # -- approvals --------------------------------------------------------------------
    async def get_or_create_approval(self, **fields: Any) -> Approval:
        async with self._db.session.begin() as s:
            existing = (
                await s.execute(
                    select(Approval).where(
                        Approval.run_id == fields["run_id"],
                        Approval.args_hash == fields["args_hash"],
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            approval = Approval(**fields)
            s.add(approval)
            # Same transaction: a visible request always comes with a waiting run.
            await s.execute(
                update(Run)
                .where(Run.id == fields["run_id"], Run.status == "running")
                .values(status="waiting_approval")
            )
            return approval

    async def get_approval(self, approval_id: uuid.UUID) -> Approval:
        async with self._db.session() as s:
            return (
                await s.execute(select(Approval).where(Approval.id == approval_id))
            ).scalar_one()

    async def expire_approval(self, approval_id: uuid.UUID) -> bool:
        async with self._db.session.begin() as s:
            result = await s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(status="expired", decided_at=utcnow())
            )
            return bool(result.rowcount)

    async def sync_waiting_status(self, run_id: uuid.UUID) -> None:
        """`waiting_approval` while any request is pending, `running` otherwise."""
        async with self._db.session.begin() as s:
            pending = await s.scalar(
                select(func.count(Approval.id)).where(
                    Approval.run_id == run_id, Approval.status == "pending"
                )
            )
            await s.execute(
                update(Run)
                .where(Run.id == run_id, Run.status.in_(("running", "waiting_approval")))
                .values(status="waiting_approval" if pending else "running")
            )

    # -- usage ------------------------------------------------------------------------
    async def record_usage(self, **fields: Any) -> None:
        async with self._db.session.begin() as s:
            s.add(LLMUsage(**fields))

    async def counters(self, run_id: uuid.UUID) -> dict[str, float]:
        async with self._db.session() as s:
            llm_calls, cost, tokens_in, tokens_out = (
                await s.execute(
                    select(
                        func.count(LLMUsage.id),
                        func.coalesce(func.sum(LLMUsage.estimated_cost_usd), 0),
                        func.coalesce(func.sum(LLMUsage.input_tokens), 0),
                        func.coalesce(func.sum(LLMUsage.output_tokens), 0),
                    ).where(LLMUsage.run_id == run_id)
                )
            ).one()
            tool_calls = await s.scalar(
                select(func.count(ToolCall.id)).where(
                    ToolCall.run_id == run_id, ToolCall.status != "rejected"
                )
            )
        return {
            "llm_calls": int(llm_calls),
            "tool_calls": int(tool_calls or 0),
            "cost_usd": float(cost),
            "input_tokens": int(tokens_in),
            "output_tokens": int(tokens_out),
        }

    # -- findings and report ----------------------------------------------------------
    async def replace_findings(
        self, run_id: uuid.UUID, step_key: str, agent: str, drafts: list[dict[str, Any]]
    ) -> None:
        """Delete-then-insert keeps a re-executed step idempotent after a worker restart."""
        async with self._db.session.begin() as s:
            await s.execute(
                delete(Finding).where(Finding.run_id == run_id, Finding.step_key == step_key)
            )
            for draft in drafts:
                s.add(Finding(run_id=run_id, step_key=step_key, agent=agent, **draft))

    async def list_findings(self, run_id: uuid.UUID) -> list[Finding]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(Finding)
                        .where(Finding.run_id == run_id)
                        .order_by(Finding.created_at, Finding.id)
                    )
                ).scalars()
            )

    async def set_finding(
        self,
        finding_id: uuid.UUID,
        status: str,
        evidence: list[dict[str, Any]],
        duplicate_of: uuid.UUID | None = None,
    ) -> None:
        async with self._db.session.begin() as s:
            await s.execute(
                update(Finding)
                .where(Finding.id == finding_id)
                .values(status=status, evidence=evidence, duplicate_of=duplicate_of)
            )

    async def list_steps(self, run_id: uuid.UUID) -> list[RunStep]:
        async with self._db.session() as s:
            return list(
                (
                    await s.execute(
                        select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.position)
                    )
                ).scalars()
            )

    async def save_report(
        self, run_id: uuid.UUID, *, partial: bool, summary: str, body: dict[str, Any], markdown: str
    ) -> None:
        async with self._db.session.begin() as s:
            await s.execute(delete(Report).where(Report.run_id == run_id))
            s.add(
                Report(
                    run_id=run_id, partial=partial, summary=summary, body=body, markdown=markdown
                )
            )


def now() -> datetime:
    return utcnow()
