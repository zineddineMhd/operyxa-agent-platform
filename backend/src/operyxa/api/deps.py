import uuid
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from operyxa.api.security import decode_access_token
from operyxa.config import Settings
from operyxa.db.models import Project, Run, User
from operyxa.db.session import Database
from operyxa.errors import NotFound, Unauthorized

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


async def owned_project(db: Database, user: User, project_id: uuid.UUID) -> Project:
    """Object-level access control. A foreign resource answers 404, never 403: no existence leak."""
    async with db.session() as s:
        project = (
            await s.execute(
                select(Project).where(Project.id == project_id, Project.owner_id == user.id)
            )
        ).scalar_one_or_none()
    if project is None:
        raise NotFound("project not found")
    return project


async def owned_run(db: Db, user: CurrentUser, run_id: uuid.UUID) -> Run:
    async with db.session() as s:
        run = (
            await s.execute(
                select(Run)
                .join(Project, Project.id == Run.project_id)
                .where(Run.id == run_id, Project.owner_id == user.id)
            )
        ).scalar_one_or_none()
    if run is None:
        raise NotFound("run not found")
    return run


OwnedRun = Annotated[Run, Depends(owned_run)]
