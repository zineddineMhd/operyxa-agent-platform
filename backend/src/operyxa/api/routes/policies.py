"""Per-project tool policies (F-10): switch a tool off, or require an approval for it.

An override can only tighten the catalog: a high-risk tool always needs an approval, a
critical one is always denied. Runs freeze the policy when they are created.
"""

import uuid

from fastapi import APIRouter
from sqlalchemy import delete, select

from operyxa.agent.store import RunStore
from operyxa.api.deps import CurrentUser, Db, owned_project
from operyxa.api.schemas import ToolPolicyIn
from operyxa.db.models import ToolPolicy, utcnow
from operyxa.db.session import Database
from operyxa.errors import AppError
from operyxa.gateway.catalog import catalog_tools, describe_catalog

router = APIRouter(tags=["mcp"])
LOCKED = {"github.get_repo"}  # the run setup pins the commit with it


async def project_policy(db: Database, project_id: uuid.UUID) -> dict[str, str]:
    async with db.session() as s:
        rows = (
            await s.execute(
                select(ToolPolicy.tool, ToolPolicy.mode).where(ToolPolicy.project_id == project_id)
            )
        ).all()
    return {tool: mode for tool, mode in rows if mode != "default"}


@router.get("/projects/{project_id}/tool-policies")
async def get_policies(project_id: uuid.UUID, db: Db, user: CurrentUser) -> dict:
    await owned_project(db, user, project_id)
    return {"tools": describe_catalog(await project_policy(db, project_id))}


@router.put("/projects/{project_id}/tool-policies")
async def set_policy(project_id: uuid.UUID, body: ToolPolicyIn, db: Db, user: CurrentUser) -> dict:
    await owned_project(db, user, project_id, "manage")
    if body.tool not in catalog_tools():
        raise AppError(
            "this tool is not in the trusted catalog", code="TOOL_UNKNOWN", status_code=422
        )
    if body.tool in LOCKED and body.mode != "default":
        raise AppError(
            "this tool is required to start a run and cannot be restricted",
            code="POLICY_LOCKED",
            status_code=422,
        )
    async with db.session.begin() as s:
        await s.execute(
            delete(ToolPolicy).where(
                ToolPolicy.project_id == project_id, ToolPolicy.tool == body.tool
            )
        )
        if body.mode != "default":
            s.add(
                ToolPolicy(
                    project_id=project_id,
                    tool=body.tool,
                    mode=body.mode,
                    updated_by=user.id,
                    updated_at=utcnow(),
                )
            )
    await RunStore(db).audit(
        user.id,
        "tool_policy.updated",
        "project",
        str(project_id),
        {"tool": body.tool, "mode": body.mode},
    )
    return {"tools": describe_catalog(await project_policy(db, project_id))}
