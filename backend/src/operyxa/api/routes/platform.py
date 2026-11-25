from datetime import timedelta

from fastapi import APIRouter
from sqlalchemy import func, select

from operyxa.api.deps import AppSettings, CurrentUser, Db
from operyxa.db.models import Approval, LLMUsage, Project, Run, utcnow
from operyxa.gateway.catalog import describe_catalog
from operyxa.llm.factory import model_names

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
        this_week = await s.scalar(
            select(func.count(Run.id)).where(
                Run.id.in_(mine), Run.created_at >= utcnow() - timedelta(days=7)
            )
        )
        pending = await s.scalar(
            select(func.count(Approval.id)).where(
                Approval.run_id.in_(mine), Approval.status == "pending"
            )
        )
    total = sum(by_status.values())
    finished = by_status.get("completed", 0) + by_status.get("failed", 0)
    return {
        "runs_total": total,
        "runs_this_week": int(this_week or 0),
        "runs_by_status": by_status,
        "success_rate": by_status.get("completed", 0) / finished if finished else None,
        "pending_approvals": int(pending or 0),
        "estimated_cost_usd": float(cost),
        "avg_run_cost_usd": float(cost) / total if total else None,
        "llm_calls": int(calls),
        "llm_provider": settings.llm_provider,
    }


@router.get("/platform/config")
async def platform_config(user: CurrentUser, settings: AppSettings) -> dict:
    """Non-secret runtime configuration the UI displays (model, default run limits)."""
    models = model_names(settings)
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": models[0] if models else "not configured",
        "llm_fallback_model": models[1] if len(models) > 1 else None,
        "private_repositories": bool(settings.secrets_key),
        "read_only": True,
        "test_runner": {
            "enabled": settings.test_runner_enabled,
            "install_network": settings.test_runner_install_network,
            "timeout_seconds": settings.test_runner_timeout_seconds,
            "approval_required": True,
        },
        "limits": {
            "max_llm_calls": settings.max_agent_steps,
            "max_tool_calls": settings.max_tool_calls,
            "max_seconds": settings.max_run_seconds,
            "max_cost_usd": settings.max_run_cost_usd,
        },
    }
