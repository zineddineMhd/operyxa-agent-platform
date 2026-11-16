import uuid
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import select, update

from operyxa.agent.store import RunStore
from operyxa.api.deps import CurrentUser, Db, OwnedRun
from operyxa.api.schemas import ApprovalDecision, ApprovalOut
from operyxa.db.models import Approval, Project, Run, RunEvent, aware, utcnow
from operyxa.errors import AppError, Conflict, NotFound

router = APIRouter(tags=["approvals"])
REVIEWER_ROLES = {"developer", "tech_lead", "admin"}


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    db: Db,
    user: CurrentUser,
    status: Literal["pending", "approved", "rejected", "expired"] | None = "pending",
) -> list:
    query = (
        select(Approval)
        .join(Run, Run.id == Approval.run_id)
        .join(Project, Project.id == Run.project_id)
        .where(Project.owner_id == user.id)
    )
    if status:
        query = query.where(Approval.status == status)
    async with db.session() as s:
        return list((await s.execute(query.order_by(Approval.requested_at).limit(100))).scalars())


@router.get("/runs/{run_id}/approvals", response_model=list[ApprovalOut])
async def run_approvals(run: OwnedRun, db: Db) -> list:
    async with db.session() as s:
        return list(
            (
                await s.execute(
                    select(Approval)
                    .where(Approval.run_id == run.id)
                    .order_by(Approval.requested_at)
                )
            ).scalars()
        )


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut)
async def decide_approval(
    approval_id: uuid.UUID, body: ApprovalDecision, db: Db, user: CurrentUser
) -> Approval:
    """Record a human decision. An approval is not an authorization bypass: the gateway has
    already checked the catalog, the agent, the arguments and the scope before asking."""
    if user.role not in REVIEWER_ROLES:
        raise AppError("your role cannot review approvals", code="FORBIDDEN", status_code=403)
    async with db.session.begin() as s:
        approval = (
            await s.execute(
                select(Approval)
                .join(Run, Run.id == Approval.run_id)
                .join(Project, Project.id == Run.project_id)
                .where(Approval.id == approval_id, Project.owner_id == user.id)
            )
        ).scalar_one_or_none()
        if approval is None:
            raise NotFound("approval not found")
        if approval.status == "pending" and utcnow() >= aware(approval.expires_at):
            await s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(status="expired", decided_at=utcnow())
            )
            expired = True
        else:
            expired = False
            status = "approved" if body.decision == "approve" else "rejected"
            # Atomic: two reviewers, or a reviewer racing the expiry, cannot both win.
            result = await s.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(status=status, decided_by=user.id, decided_at=utcnow(), reason=body.reason)
            )
            if not result.rowcount:
                raise Conflict("this request is no longer pending", code="APPROVAL_NOT_PENDING")
            s.add(
                RunEvent(
                    run_id=approval.run_id,
                    type="approval.decided",
                    data={"approval_id": str(approval_id), "decision": status},
                )
            )
    store = RunStore(db)
    if expired:
        raise Conflict("this request has expired", code="APPROVAL_EXPIRED")
    await store.audit(
        user.id,
        f"approval.{status}",
        "approval",
        str(approval_id),
        {"run_id": str(approval.run_id), "tool": approval.tool, "args_hash": approval.args_hash},
    )
    return await store.get_approval(approval_id)
