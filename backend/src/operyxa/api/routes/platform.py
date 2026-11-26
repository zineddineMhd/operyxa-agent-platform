import asyncio
from datetime import timedelta
from urllib.parse import urlsplit

from fastapi import APIRouter
from sqlalchemy import func, select

from operyxa.agent.metrics import platform_metrics
from operyxa.agent.workflows import describe_builtin
from operyxa.api.deps import AppSettings, CurrentUser, Db, accessible_projects
from operyxa.bootstrap import build_providers
from operyxa.db.models import Approval, LLMUsage, Run, utcnow
from operyxa.gateway.catalog import CATALOG, describe_catalog
from operyxa.llm.factory import model_names, provider_chain

router = APIRouter(tags=["platform"])


@router.get("/mcp/tools")
async def list_tools(user: CurrentUser) -> dict:
    """Trusted tool catalog with the policy the gateway enforces for each tool."""
    return {"tools": describe_catalog()}


def _servers(settings) -> list[dict]:
    remote = {
        "github": settings.mcp_github_url,
        "analysis": settings.mcp_analysis_url,
        "changes": settings.mcp_changes_url,
        "test_runner": settings.mcp_test_runner_url,
    }
    out = []
    for name, tools in CATALOG.items():
        enabled = name != "test_runner" or settings.test_runner_enabled
        if name == "changes":
            enabled = settings.pull_requests_enabled
        url = remote.get(name) or ""
        out.append(
            {
                "name": name,
                "enabled": enabled,
                "transport": "streamable-http" if url else "in-process",
                # Only the host is shown: a URL may carry credentials.
                "host": urlsplit(url).hostname if url else None,
                "trusted_tools": sorted(f"{name}.{t}" for t in tools),
            }
        )
    return out


@router.get("/mcp/servers")
async def list_servers(user: CurrentUser, settings: AppSettings, probe: bool = False) -> dict:
    """MCP Hub (F-10): connected servers. With `probe`, each server is asked for its tools; the
    tools it advertises outside the trusted catalog are listed as ignored."""
    servers = _servers(settings)
    if probe:
        providers = {p.name: p for p in build_providers(settings)}
        for server in servers:
            provider = providers.get(server["name"])
            if provider is None:
                server["status"] = "disabled"
                continue
            try:
                async with asyncio.timeout(10):
                    advertised = {f"{server['name']}.{t.name}" for t in await provider.list_tools()}
            except Exception as exc:  # unreachable server: shown, not raised
                server["status"], server["error"] = "unreachable", type(exc).__name__
                continue
            trusted = set(server["trusted_tools"])
            server["status"] = "ok"
            server["available_tools"] = sorted(advertised & trusted)
            server["missing_tools"] = sorted(trusted - advertised)
            server["ignored_tools"] = sorted(advertised - trusted)
    return {"servers": servers}


@router.get("/analytics/overview")
async def overview(db: Db, user: CurrentUser, settings: AppSettings) -> dict:
    mine = select(Run.id).where(Run.project_id.in_(accessible_projects(user)))
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
        **await platform_metrics(db, mine),
    }


@router.get("/platform/config")
async def platform_config(user: CurrentUser, settings: AppSettings) -> dict:
    """Non-secret runtime configuration the UI displays (model, default run limits)."""
    models = model_names(settings)
    return {
        "llm_provider": settings.llm_provider,
        "llm_model": models[0] if models else "not configured",
        # Every model tried after the main one, the backup provider's included.
        "llm_fallback_model": ", ".join(models[1:]) or None,
        "llm_chain": [{"provider": p, "model": m} for p, m in provider_chain(settings)],
        "private_repositories": bool(settings.secrets_key),
        "read_only": True,
        # Agents never write. A pull request is created only after a person confirms it.
        "pull_requests": {
            "enabled": settings.pull_requests_enabled,
            "confirmation_required": True,
        },
        "workflows": describe_builtin(),
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
