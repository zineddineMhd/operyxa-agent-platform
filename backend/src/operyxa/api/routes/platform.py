from fastapi import APIRouter
from sqlalchemy import func, select

from operyxa.api.deps import AppSettings, CurrentUser, Db
from operyxa.db.models import LLMUsage, Project, Run
from operyxa.gateway.catalog import describe_catalog

router = APIRouter(tags=["platform"])


@router.get("/mcp/tools")
async def list_tools(user: CurrentUser) -> dict:
    """Trusted tool catalog with the policy the gateway enforces for each tool."""
    return {"tools": describe_catalog()}


@router.get("/analytics/overview")
async def overview(db: Db, user: CurrentUser, settings: AppSettings) -> dict:
    mine = (
        select(Run.id)
        .join(Project, Project.id == Run.project_id)
        .where(Project.owner_id == user.id)
    )
    async with db.session() as s:
        by_status = dict(
            (
                await s.execute(
                    select(Run.status, func.count(Run.id))
                    .where(Run.id.in_(mine))
                    .group_by(Run.status)
                )
            ).all()
        )
        cost, calls = (
            await s.execute(
                select(
                    func.coalesce(func.sum(LLMUsage.estimated_cost_usd), 0), func.count(LLMUsage.id)
                ).where(LLMUsage.run_id.in_(mine))
            )
        ).one()
    total = sum(by_status.values())
    finished = by_status.get("completed", 0) + by_status.get("failed", 0)
    return {
        "runs_total": total,
        "runs_by_status": by_status,
        "success_rate": by_status.get("completed", 0) / finished if finished else None,
        "pending_approvals": by_status.get("waiting_approval", 0),
        "estimated_cost_usd": float(cost),
        "avg_run_cost_usd": float(cost) / total if total else None,
        "llm_calls": int(calls),
        "llm_provider": settings.llm_provider,
    }
