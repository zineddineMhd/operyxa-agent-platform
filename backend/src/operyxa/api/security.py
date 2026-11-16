import uuid
from datetime import timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from operyxa.db.models import utcnow
from operyxa.errors import Unauthorized

_hasher = PasswordHasher()
# Verified when the email is unknown, so login timing does not reveal which accounts exist.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (Argon2Error, InvalidHashError):
        return False


def create_access_token(user_id: uuid.UUID, secret: str, ttl_minutes: int) -> str:
    now = utcnow()
    return jwt.encode(
        {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=ttl_minutes)},
        secret,
        algorithm="HS256",
    )


def decode_access_token(token: str, secret: str) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]}
        )
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise Unauthorized("invalid or expired token") from exc
