"""Tests for /auth router — signup, login, logout, /me."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth import create_access_token, get_current_user
from app.database import get_db
from app.main import app
from app.models.db_models import User


def _fake_user(email: str = "user@test.com") -> MagicMock:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.email = email
    user.request_limit = 5
    user.requests_used = 1
    return user


def _make_db(user: MagicMock | None = None, existing: MagicMock | None = None):
    mock_db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    mock_db.execute = AsyncMock(return_value=result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.refresh = AsyncMock(side_effect=lambda u: None)
    return mock_db


async def _override_db(mock_db):
    async def _dep():
        yield mock_db
    return _dep


class TestSignup:
    @pytest.mark.asyncio
    async def test_signup_success(self):
        new_user = _fake_user("new@test.com")
        mock_db = _make_db()

        # After refresh, user has the right attrs
        async def fake_refresh(u):
            u.id = new_user.id
            u.email = "new@test.com"
            u.request_limit = 5
            u.requests_used = 0

        mock_db.refresh = AsyncMock(side_effect=fake_refresh)

        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "new@test.com", "password": "Secure1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 201
        assert "Signup" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_signup_duplicate_email_returns_409(self):
        existing = _fake_user("dup@test.com")
        mock_db = _make_db(existing=existing)

        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "dup@test.com", "password": "Secure1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_signup_weak_password_returns_400(self):
        mock_db = _make_db()
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "weak@test.com", "password": "weak"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_signup_invalid_email_returns_400(self):
        mock_db = _make_db()
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "not-an-email", "password": "Secure1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 400


class TestLogin:
    @pytest.mark.asyncio
    async def test_login_success_sets_cookie(self):
        from app.auth import hash_password
        pw_hash, pw_salt = hash_password("Secure1!pass")
        user = _fake_user("login@test.com")
        user.password_hash = pw_hash
        user.password_salt = pw_salt

        mock_db = _make_db(existing=user)
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/login",
                json={"email": "login@test.com", "password": "Secure1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 200
        assert "access_token" in response.cookies

    @pytest.mark.asyncio
    async def test_login_wrong_password_returns_401(self):
        from app.auth import hash_password
        pw_hash, pw_salt = hash_password("Correct1!pass")
        user = _fake_user("login2@test.com")
        user.password_hash = pw_hash
        user.password_salt = pw_salt

        mock_db = _make_db(existing=user)
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/login",
                json={"email": "login2@test.com", "password": "Wrong1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_user_not_found_returns_401(self):
        mock_db = _make_db(existing=None)
        app.dependency_overrides[get_db] = await _override_db(mock_db)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/auth/login",
                json={"email": "ghost@test.com", "password": "Secure1!pass"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 401


class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/auth/logout")
        assert response.status_code == 200
        assert response.json()["message"] == "Logged out"


class TestMe:
    @pytest.mark.asyncio
    async def test_me_returns_user_info(self):
        fake_user = _fake_user("me@test.com")

        async def override_user():
            return fake_user

        app.dependency_overrides[get_current_user] = override_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/auth/me")

        app.dependency_overrides.clear()
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "me@test.com"
        assert "remaining_requests" in data
