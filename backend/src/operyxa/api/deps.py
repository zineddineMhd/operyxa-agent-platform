import uuid
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import or_, select

from operyxa.api.security import decode_access_token
from operyxa.config import Settings
from operyxa.db.models import Project, Run, TeamMember, User
from operyxa.db.session import Database
from operyxa.errors import AppError, NotFound, Unauthorized

_bearer = HTTPBearer(auto_error=False)


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


Db = Annotated[Database, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_app_settings)]


async def current_user(
    db: Db,
    settings: AppSettings,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if creds is None:
        raise Unauthorized("missing bearer token")
    user_id = decode_access_token(creds.credentials, settings.jwt_secret_key)
    async with db.session() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise Unauthorized("unknown user")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


class Paging:
    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset


# What each level may do on a project (F-13). The project owner is always "owner".
#   read: see runs, reports, evidence      run: start/cancel runs, decide approvals
#   manage: tokens, tool policies, team, confirm pull requests
LEVELS = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
NEEDED = {"read": 0, "run": 1, "manage": 2}


def accessible_projects(user: User):
    """Ids of the projects a user owns or reaches through a team: the base of every listing."""
    teams = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    return select(Project.id).where(or_(Project.owner_id == user.id, Project.team_id.in_(teams)))


async def project_role(db: Database, user: User, project: Project) -> str | None:
    if project.owner_id == user.id:
        return "owner"
    if project.team_id is None:
        return None
    async with db.session() as s:
        return await s.scalar(
            select(TeamMember.role).where(
                TeamMember.team_id == project.team_id, TeamMember.user_id == user.id
            )
        )


def require(role: str | None, action: str) -> None:
    if role is None or LEVELS[role] < NEEDED[action]:
        raise AppError(
            f"your role on this project does not allow this ({action})",
            code="FORBIDDEN",
            status_code=403,
        )


async def owned_project(
    db: Database, user: User, project_id: uuid.UUID, action: str = "read"
) -> Project:
    """Object-level access control. A project the user cannot see answers 404, never 403: no
    existence leak. A visible project the user's role cannot change answers 403."""
    async with db.session() as s:
        project = (
            await s.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
    role = await project_role(db, user, project) if project is not None else None
    if project is None or role is None:
        raise NotFound("project not found")
    require(role, action)
    return project


async def owned_run(db: Db, user: CurrentUser, run_id: uuid.UUID) -> Run:
    async with db.session() as s:
        run = (
            await s.execute(
                select(Run).where(Run.id == run_id, Run.project_id.in_(accessible_projects(user)))
            )
        ).scalar_one_or_none()
    if run is None:
        raise NotFound("run not found")
    return run


async def run_role(db: Database, user: User, run: Run) -> str | None:
    async with db.session() as s:
        project = (
            await s.execute(select(Project).where(Project.id == run.project_id))
        ).scalar_one()
    return await project_role(db, user, project)


OwnedRun = Annotated[Run, Depends(owned_run)]
