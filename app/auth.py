import base64
import hashlib
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.db_models import User
from app.services.cache_service import get_global_rate_limiter


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _token_secret() -> bytes:
    return settings.auth_secret_key.encode("utf-8")


def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        settings.auth_pbkdf2_iterations,
    )
    return digest.hex(), salt.hex()


def verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
    computed_hash_hex, _ = hash_password(password=password, salt_hex=salt_hex)
    return hmac.compare_digest(computed_hash_hex, expected_hash_hex)


def create_access_token(user_id: uuid.UUID, email: str) -> str:
    expiry = datetime.now(UTC) + timedelta(minutes=settings.auth_token_exp_minutes)
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": int(expiry.timestamp()),
    }
    payload_raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_segment = _b64url_encode(payload_raw)
    signature = hmac.new(_token_secret(), payload_segment.encode("utf-8"), hashlib.sha256).digest()
    signature_segment = _b64url_encode(signature)
    return f"{payload_segment}.{signature_segment}"


def decode_access_token(token: str) -> dict:
    try:
        payload_segment, signature_segment = token.split(".", maxsplit=1)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from exc

    expected_signature = hmac.new(
        _token_secret(),
        payload_segment.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    actual_signature = _b64url_decode(signature_segment)

    if not hmac.compare_digest(actual_signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token")

    try:
        payload = json.loads(_b64url_decode(payload_segment).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from exc
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp < int(datetime.now(UTC).timestamp()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return payload


@dataclass(frozen=True)
class UserQuota:
    user_id: uuid.UUID
    email: str
    remaining_requests: int


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    access_token = request.cookies.get(settings.auth_cookie_name)
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_access_token(access_token)
    sub = payload.get("sub")
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token") from exc

    try:
        result = await db.execute(select(User).where(User.id == user_id))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def consume_user_request_quota(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserQuota:
    if settings.app_env == "development":
        return UserQuota(user_id=user.id, email=user.email, remaining_requests=9999)

    # ── Global daily cap (in-process, resets at UTC midnight) ─────────────
    if not get_global_rate_limiter().check_and_increment():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily service limit reached. Please try again tomorrow.",
        )

    quota_update = (
        update(User)
        .where(User.id == user.id)
        .where(User.requests_used < User.request_limit)
        .values(requests_used=User.requests_used + 1)
        .returning(User.request_limit, User.requests_used)
    )
    updated = await db.execute(quota_update)
    quota_row = updated.one_or_none()
    if quota_row is None:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Request limit reached. Please contact admin to increase quota.",
        )

    await db.commit()
    request_limit, requests_used = quota_row
    return UserQuota(
        user_id=user.id,
        email=user.email,
        remaining_requests=max(0, request_limit - requests_used),
    )
