from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from operyxa.api.deps import AppSettings, CurrentUser, Db
from operyxa.api.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from operyxa.api.security import create_access_token, hash_password, verify_password
from operyxa.db.models import User
from operyxa.errors import Conflict, Unauthorized

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, db: Db) -> User:
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    try:
        async with db.session.begin() as s:
            s.add(user)
    except IntegrityError as exc:
        raise Conflict("this email is already registered", code="EMAIL_TAKEN") from exc
    return user


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, db: Db, settings: AppSettings) -> TokenOut:
    async with db.session() as s:
        user = (
            await s.execute(select(User).where(User.email == body.email.lower()))
        ).scalar_one_or_none()
    if not verify_password(body.password, user.password_hash if user else None) or user is None:
        raise Unauthorized("invalid email or password", code="INVALID_CREDENTIALS")
    token = create_access_token(user.id, settings.jwt_secret_key, settings.jwt_ttl_minutes)
    return TokenOut(access_token=token, expires_in=settings.jwt_ttl_minutes * 60)


@router.get("/users/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
