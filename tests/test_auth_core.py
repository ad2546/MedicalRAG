"""Tests for app/auth.py — token creation, password hashing, quota logic."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth import (
    consume_user_request_quota,
    create_access_token,
    decode_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_and_verify_correct_password(self):
        hashed, salt = hash_password("Correct1!")
        assert verify_password("Correct1!", hashed, salt) is True

    def test_verify_wrong_password_fails(self):
        hashed, salt = hash_password("Correct1!")
        assert verify_password("Wrong1!", hashed, salt) is False

    def test_same_password_different_salt_different_hash(self):
        h1, s1 = hash_password("Password1!")
        h2, s2 = hash_password("Password1!")
        # Different salts → different hashes
        assert s1 != s2
        assert h1 != h2

    def test_hash_with_provided_salt_is_deterministic(self):
        h1, s1 = hash_password("Password1!")
        h2, _ = hash_password("Password1!", salt_hex=s1)
        assert h1 == h2


# ---------------------------------------------------------------------------
# Token create / decode
# ---------------------------------------------------------------------------

class TestAccessToken:
    def test_round_trip(self):
        uid = uuid.uuid4()
        token = create_access_token(uid, "test@example.com")
        payload = decode_access_token(token)
        assert payload["sub"] == str(uid)
        assert payload["email"] == "test@example.com"

    def test_tampered_signature_raises_401(self):
        uid = uuid.uuid4()
        token = create_access_token(uid, "a@b.com")
        # Corrupt the signature segment
        parts = token.split(".")
        tampered = parts[0] + ".INVALIDSIG"
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(tampered)
        assert exc_info.value.status_code == 401

    def test_malformed_token_no_dot_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token("notavalidtoken")
        assert exc_info.value.status_code == 401

    def test_expired_token_raises_401(self, monkeypatch):
        from datetime import UTC, datetime


        # Patch timedelta so expiry is in the past
        def make_expired_token(user_id, email):
            import base64
            import hashlib
            import hmac
            import json

            from app.config import settings

            expiry = datetime(2000, 1, 1, tzinfo=UTC)
            payload = {"sub": str(user_id), "email": email, "exp": int(expiry.timestamp())}
            payload_raw = json.dumps(payload, separators=(",", ":")).encode()
            payload_seg = base64.urlsafe_b64encode(payload_raw).decode().rstrip("=")
            secret = settings.auth_secret_key.encode()
            sig = hmac.new(secret, payload_seg.encode(), hashlib.sha256).digest()
            sig_seg = base64.urlsafe_b64encode(sig).decode().rstrip("=")
            return f"{payload_seg}.{sig_seg}"

        token = make_expired_token(uuid.uuid4(), "x@y.com")
        with pytest.raises(HTTPException) as exc_info:
            decode_access_token(token)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# get_current_user
# ---------------------------------------------------------------------------

class TestGetCurrentUser:
    @pytest.mark.asyncio
    async def test_missing_cookie_raises_401(self):
        mock_request = MagicMock()
        mock_request.cookies.get.return_value = None
        mock_db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request=mock_request, db=mock_db)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_user_not_found_raises_401(self):
        uid = uuid.uuid4()
        token = create_access_token(uid, "missing@example.com")
        mock_request = MagicMock()
        mock_request.cookies.get.return_value = token

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(request=mock_request, db=mock_db)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_returns_user(self):
        uid = uuid.uuid4()
        token = create_access_token(uid, "valid@example.com")
        mock_request = MagicMock()
        mock_request.cookies.get.return_value = token

        fake_user = MagicMock(id=uid, email="valid@example.com")
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = fake_user
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        user = await get_current_user(request=mock_request, db=mock_db)
        assert user == fake_user


# ---------------------------------------------------------------------------
# consume_user_request_quota
# ---------------------------------------------------------------------------

class TestConsumeUserRequestQuota:
    @pytest.mark.asyncio
    async def test_dev_mode_bypasses_quota(self, monkeypatch):
        monkeypatch.setattr("app.auth.settings.app_env", "development")
        fake_user = MagicMock(id=uuid.uuid4(), email="dev@test.com")
        mock_db = AsyncMock()

        quota = await consume_user_request_quota(user=fake_user, db=mock_db)
        assert quota.remaining_requests == 9999

    @pytest.mark.asyncio
    async def test_global_rate_limit_exceeded_raises_429(self, monkeypatch):
        monkeypatch.setattr("app.auth.settings.app_env", "production")
        fake_user = MagicMock(id=uuid.uuid4(), email="prod@test.com")
        mock_db = AsyncMock()

        mock_limiter = MagicMock()
        mock_limiter.check_and_increment.return_value = False

        with patch("app.auth.get_global_rate_limiter", return_value=mock_limiter), \
             pytest.raises(HTTPException) as exc_info:
            await consume_user_request_quota(user=fake_user, db=mock_db)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_user_quota_exceeded_raises_429(self, monkeypatch):
        monkeypatch.setattr("app.auth.settings.app_env", "production")
        fake_user = MagicMock(id=uuid.uuid4(), email="quota@test.com")

        mock_limiter = MagicMock()
        mock_limiter.check_and_increment.return_value = True

        mock_updated = MagicMock()
        mock_updated.one_or_none.return_value = None  # quota exhausted

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_updated)
        mock_db.rollback = AsyncMock()

        with patch("app.auth.get_global_rate_limiter", return_value=mock_limiter), \
             pytest.raises(HTTPException) as exc_info:
            await consume_user_request_quota(user=fake_user, db=mock_db)
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_successful_quota_decrement_returns_remaining(self, monkeypatch):
        monkeypatch.setattr("app.auth.settings.app_env", "production")
        fake_user = MagicMock(id=uuid.uuid4(), email="ok@test.com")

        mock_limiter = MagicMock()
        mock_limiter.check_and_increment.return_value = True

        mock_updated = MagicMock()
        mock_updated.one_or_none.return_value = (5, 3)  # request_limit=5, requests_used=3

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_updated)
        mock_db.commit = AsyncMock()

        with patch("app.auth.get_global_rate_limiter", return_value=mock_limiter):
            quota = await consume_user_request_quota(user=fake_user, db=mock_db)

        assert quota.remaining_requests == 2
