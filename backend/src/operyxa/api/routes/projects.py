import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from operyxa.api.deps import CurrentUser, Db, Paging, owned_project
from operyxa.api.schemas import Page, ProjectIn, ProjectOut
from operyxa.db.models import Project

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectIn, db: Db, user: CurrentUser) -> Project:
    owner, name = body.repo()
    project = Project(owner_id=user.id, name=body.name, repo_owner=owner, repo_name=name)
    async with db.session.begin() as s:
        s.add(project)
    return project


@router.get("/projects", response_model=Page[ProjectOut])
async def list_projects(db: Db, user: CurrentUser, paging: Paging = Depends()) -> dict:
    where = Project.owner_id == user.id
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
async def get_project(project_id: uuid.UUID, db: Db, user: CurrentUser) -> Project:
    return await owned_project(db, user, project_id)
