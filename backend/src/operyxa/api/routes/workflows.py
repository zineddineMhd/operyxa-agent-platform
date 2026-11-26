"""Workflows (F-13): built-in templates plus workflows saved by a user or a team."""

import uuid

from fastapi import APIRouter
from sqlalchemy import or_, select

from operyxa.agent.store import RunStore
from operyxa.agent.workflows import BUILTIN, describe_builtin
from operyxa.api.deps import CurrentUser, Db
from operyxa.api.routes.teams import team_role
from operyxa.api.schemas import WorkflowIn, WorkflowOut
from operyxa.db.models import TeamMember, User, Workflow
from operyxa.db.session import Database
from operyxa.errors import AppError, NotFound

router = APIRouter(tags=["workflows"])


def _visible(user: User):
    teams = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    return or_(Workflow.owner_id == user.id, Workflow.team_id.in_(teams))


async def visible_workflow(db: Database, user: User, workflow_id: uuid.UUID) -> Workflow:
    async with db.session() as s:
        workflow = (
            await s.execute(select(Workflow).where(Workflow.id == workflow_id, _visible(user)))
        ).scalar_one_or_none()
    if workflow is None:
        raise NotFound("workflow not found")
    return workflow


@router.get("/workflows")
async def list_workflows(db: Db, user: CurrentUser) -> dict:
    async with db.session() as s:
        saved = list(
            (
                await s.execute(
                    select(Workflow).where(_visible(user)).order_by(Workflow.created_at)
                )
            ).scalars()
        )
    return {
        "builtin": describe_builtin(),
        "saved": [WorkflowOut.model_validate(w).model_dump(mode="json") for w in saved],
    }


@router.post("/workflows", response_model=WorkflowOut, status_code=201)
async def create_workflow(body: WorkflowIn, db: Db, user: CurrentUser) -> Workflow:
    base = BUILTIN.get(body.base_type)
    if base is None:
        raise AppError("unknown base workflow", code="WORKFLOW_UNKNOWN", status_code=422)
    agents = body.agents if body.agents is not None else list(base.agents)
    extra = set(agents) - set(base.agents) - {"testing", "remediation"}
    if extra or not agents:
        # A saved workflow narrows its base; the optional agents come with their option.
        raise AppError(
            f"agents must be a non-empty subset of {sorted(base.agents)}",
            code="WORKFLOW_INVALID",
            status_code=422,
        )
    if body.team_id is not None:
        role = await team_role(db, user, body.team_id)
        if role not in ("owner", "admin", "member"):
            raise NotFound("team not found")
    workflow = Workflow(
        owner_id=user.id,
        team_id=body.team_id,
        name=body.name,
        description=body.description,
        base_type=body.base_type,
        objective_template=body.objective_template,
        agents=[a for a in agents if a not in ("testing", "remediation")],
        options={"run_tests": body.run_tests, "propose_fixes": body.propose_fixes},
    )
    async with db.session.begin() as s:
        s.add(workflow)
    await RunStore(db).audit(user.id, "workflow.created", "workflow", str(workflow.id))
    return workflow


@router.delete("/workflows/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: uuid.UUID, db: Db, user: CurrentUser) -> None:
    workflow = await visible_workflow(db, user, workflow_id)
    if workflow.owner_id != user.id and (
        workflow.team_id is None
        or await team_role(db, user, workflow.team_id) not in ("owner", "admin")
    ):
        raise AppError(
            "only its author or a team admin can delete it", code="FORBIDDEN", status_code=403
        )
    async with db.session.begin() as s:
        await s.delete(await s.get(Workflow, workflow_id))
