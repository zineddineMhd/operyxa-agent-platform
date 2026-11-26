"""Teams (F-13). A project shared with a team is reachable by its members, by role:
viewer (read), member (run, approve), admin (manage). The project owner keeps full control."""

import uuid

from fastapi import APIRouter
from sqlalchemy import delete, select, update

from operyxa.agent.store import RunStore
from operyxa.api.deps import CurrentUser, Db
from operyxa.api.schemas import TeamIn, TeamMemberIn, TeamOut
from operyxa.db.models import Team, TeamMember, User
from operyxa.db.session import Database
from operyxa.errors import AppError, Conflict, NotFound

router = APIRouter(tags=["teams"])


async def team_role(db: Database, user: User, team_id: uuid.UUID) -> str | None:
    async with db.session() as s:
        return await s.scalar(
            select(TeamMember.role).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user.id
            )
        )


async def _team_out(db: Database, team: Team, role: str | None) -> dict:
    async with db.session() as s:
        members = (
            await s.execute(
                select(TeamMember.user_id, User.email, TeamMember.role)
                .join(User, User.id == TeamMember.user_id)
                .where(TeamMember.team_id == team.id)
                .order_by(TeamMember.created_at)
            )
        ).all()
    return {
        "id": team.id,
        "name": team.name,
        "created_at": team.created_at,
        "role": role,
        "members": [{"user_id": u, "email": e, "role": r} for u, e, r in members],
    }


async def _managed(db: Database, user: User, team_id: uuid.UUID) -> Team:
    role = await team_role(db, user, team_id)
    if role is None:
        raise NotFound("team not found")
    if role not in ("owner", "admin"):
        raise AppError("only a team owner or admin can do this", code="FORBIDDEN", status_code=403)
    async with db.session() as s:
        return (await s.execute(select(Team).where(Team.id == team_id))).scalar_one()


@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(body: TeamIn, db: Db, user: CurrentUser) -> dict:
    team = Team(name=body.name, created_by=user.id)
    async with db.session.begin() as s:
        s.add(team)
        await s.flush()
        s.add(TeamMember(team_id=team.id, user_id=user.id, role="owner"))
    await RunStore(db).audit(user.id, "team.created", "team", str(team.id))
    return await _team_out(db, team, "owner")


@router.get("/teams", response_model=list[TeamOut])
async def list_teams(db: Db, user: CurrentUser) -> list:
    async with db.session() as s:
        rows = (
            await s.execute(
                select(Team, TeamMember.role)
                .join(TeamMember, TeamMember.team_id == Team.id)
                .where(TeamMember.user_id == user.id)
                .order_by(Team.created_at)
            )
        ).all()
    return [await _team_out(db, team, role) for team, role in rows]


@router.post("/teams/{team_id}/members", response_model=TeamOut)
async def add_member(team_id: uuid.UUID, body: TeamMemberIn, db: Db, user: CurrentUser) -> dict:
    team = await _managed(db, user, team_id)
    async with db.session.begin() as s:
        member = (
            await s.execute(select(User).where(User.email == body.email.lower()))
        ).scalar_one_or_none()
        if member is None:
            # The person must register first; no invitation e-mail is sent by the platform.
            raise NotFound("no account with this email", code="USER_NOT_FOUND")
        current = await s.scalar(
            select(TeamMember.role).where(
                TeamMember.team_id == team_id, TeamMember.user_id == member.id
            )
        )
        if current == "owner":
            raise Conflict("the team owner's role cannot be changed", code="TEAM_OWNER")
        if current is None:
            s.add(TeamMember(team_id=team_id, user_id=member.id, role=body.role))
        else:
            await s.execute(
                update(TeamMember)
                .where(TeamMember.team_id == team_id, TeamMember.user_id == member.id)
                .values(role=body.role)
            )
    await RunStore(db).audit(
        user.id,
        "team.member_set",
        "team",
        str(team_id),
        {"user_id": str(member.id), "role": body.role},
    )
    return await _team_out(db, team, await team_role(db, user, team_id))


@router.delete("/teams/{team_id}/members/{user_id}", response_model=TeamOut)
async def remove_member(team_id: uuid.UUID, user_id: uuid.UUID, db: Db, user: CurrentUser) -> dict:
    team = await _managed(db, user, team_id)
    async with db.session.begin() as s:
        role = await s.scalar(
            select(TeamMember.role).where(
                TeamMember.team_id == team_id, TeamMember.user_id == user_id
            )
        )
        if role is None:
            raise NotFound("member not found")
        if role == "owner":
            raise Conflict("the team owner cannot be removed", code="TEAM_OWNER")
        await s.execute(
            delete(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id)
        )
    await RunStore(db).audit(
        user.id, "team.member_removed", "team", str(team_id), {"user_id": str(user_id)}
    )
    return await _team_out(db, team, await team_role(db, user, team_id))
