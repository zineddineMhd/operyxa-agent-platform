"""Encryption at rest for the few secrets the platform must keep (GitHub tokens)."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from operyxa.errors import AppError


class SecretBox:
    def __init__(self, key: str):
        self._fernet = (
            Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())) if key else None
        )

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            raise AppError(
                "SECRETS_KEY is not configured on the server: tokens cannot be stored",
                code="SECRET_STORAGE_UNAVAILABLE",
                status_code=503,
            )
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        if self._fernet is None:
            raise RuntimeError("SECRETS_KEY is not configured")
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("stored secret cannot be decrypted (SECRETS_KEY changed?)") from exc
