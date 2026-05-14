"""JWT issuance + verification, and password hashing."""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from macro_trader.config import Settings
from macro_trader.utils.dates import utcnow

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(subject: str, settings: Settings, **extra: Any) -> str:
    """Issue a signed JWT access token for `subject` (typically a user id)."""
    now = utcnow()
    exp = now + timedelta(minutes=settings.auth.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "type": "access",
        **extra,
    }
    return jwt.encode(payload, settings.auth.jwt_secret_key, algorithm=settings.auth.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> dict[str, Any] | None:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.auth.jwt_secret_key,
            algorithms=[settings.auth.jwt_algorithm],
        )
    except JWTError:
        return None
    if payload.get("type") != "access":
        return None
    return payload


def create_refresh_token() -> tuple[str, str]:
    """Return ``(raw_token, hash_to_store)``. The raw token is sent to the
    client; the hash is stored in ``auth.refresh_tokens``."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_password(raw)


def verify_refresh_token(raw: str, hashed: str) -> bool:
    return verify_password(raw, hashed)
