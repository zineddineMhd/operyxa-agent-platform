import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update

from operyxa.agent.store import RunStore
from operyxa.api.deps import (
    AppSettings,
    CurrentUser,
    Db,
    Paging,
    accessible_projects,
    owned_project,
    project_role,
)
from operyxa.api.routes.teams import team_role
from operyxa.api.schemas import Page, ProjectIn, ProjectOut, ProjectUpdate
from operyxa.crypto import SecretBox
from operyxa.db.models import Project
from operyxa.errors import NotFound

router = APIRouter(tags=["projects"])


async def _check_team(db, user, team_id: uuid.UUID) -> None:
    # Sharing a project with a team needs a manager role in that team.
    if await team_role(db, user, team_id) not in ("owner", "admin"):
        raise NotFound("team not found")


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectIn, db: Db, user: CurrentUser, settings: AppSettings
) -> Project:
    owner, name = body.repo()
    project = Project(owner_id=user.id, name=body.name, repo_owner=owner, repo_name=name)
    box = SecretBox(settings.secrets_key)
    if body.github_token is not None:
        project.github_token_encrypted = box.encrypt(body.github_token.get_secret_value())
    if body.github_write_token is not None:
        project.github_write_token_encrypted = box.encrypt(
            body.github_write_token.get_secret_value()
        )
    if body.team_id is not None:
        await _check_team(db, user, body.team_id)
        project.team_id = body.team_id
    async with db.session.begin() as s:
        s.add(project)
    return project


@router.get("/projects", response_model=Page[ProjectOut])
async def list_projects(db: Db, user: CurrentUser, paging: Paging = Depends()) -> dict:
    where = Project.id.in_(accessible_projects(user))
    async with db.session() as s:
        total = await s.scalar(select(func.count(Project.id)).where(where))
        items = (
            await s.execute(
                select(Project)
                .where(where)
                .order_by(Project.created_at.desc(), Project.id)
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


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: Db, user: CurrentUser) -> dict:
    project = await owned_project(db, user, project_id)
    role = await project_role(db, user, project)
    return {**ProjectOut.model_validate(project).model_dump(), "role": role}


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, db: Db, user: CurrentUser, settings: AppSettings
) -> Project:
    """Rename, rotate or remove a token, share with a team. Tokens stay write-only."""
    project = await owned_project(db, user, project_id, "manage")
    values: dict = {}
    box = SecretBox(settings.secrets_key)
    if body.name is not None:
        values["name"] = body.name
    for field, column in (
        ("github_token", "github_token_encrypted"),
        ("github_write_token", "github_write_token_encrypted"),
    ):
        secret = getattr(body, field)
        if secret is not None:
            value = secret.get_secret_value().strip()
            values[column] = box.encrypt(value) if value else None
    if body.clear_team:
        values["team_id"] = None
    elif body.team_id is not None:
        await _check_team(db, user, body.team_id)
        values["team_id"] = body.team_id
    if values:
        async with db.session.begin() as s:
            await s.execute(update(Project).where(Project.id == project.id).values(**values))
        changed = sorted(k.replace("_encrypted", "") for k in values)
        await RunStore(db).audit(
            user.id, "project.updated", "project", str(project.id), {"fields": changed}
        )
    return await owned_project(db, user, project_id)
