import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from operyxa.api.deps import AppSettings, CurrentUser, Db
from operyxa.api.schemas import LoginIn, RegisterIn, TokenOut, UserOut
from operyxa.api.security import create_access_token, hash_password, verify_password
from operyxa.db.models import User
from operyxa.errors import AppError, Conflict, Unauthorized

router = APIRouter(tags=["auth"])


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"


class SlidingWindow:
    """Attempts per key over the last minute, in memory (one API process).

    Several API replicas would each keep their own count: enough against password guessing
    on a single machine; a shared store (PostgreSQL, Redis) is needed beyond that.
    """

    def __init__(self, max_keys: int = 10_000):
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.max_keys = max_keys

    def allow(self, key: str, limit: int, now: float | None = None) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        if len(self.hits) > self.max_keys:
            self.hits.clear()
        window = self.hits[key]
        while window and window[0] <= now - 60:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True


def _limit(request: Request, key: str, limit: int) -> None:
    if not request.app.state.auth_limiter.allow(key, limit):
        raise RateLimited("too many attempts, try again in a minute")


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/auth/register", response_model=UserOut, status_code=201)
async def register(body: RegisterIn, db: Db, request: Request, settings: AppSettings) -> User:
    _limit(request, "register:" + _client(request), settings.auth_rate_limit_per_minute)
    user = User(email=body.email.lower(), password_hash=hash_password(body.password))
    try:
        async with db.session.begin() as s:
            s.add(user)
    except IntegrityError as exc:
        raise Conflict("this email is already registered", code="EMAIL_TAKEN") from exc
    return user


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, db: Db, settings: AppSettings, request: Request) -> TokenOut:
    _limit(request, "login:" + body.email.lower(), settings.auth_rate_limit_per_minute)
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
