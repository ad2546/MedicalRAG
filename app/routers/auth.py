import re

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import settings
from app.database import get_db
from app.models.db_models import User
from app.models.schemas import AuthResponse, LoginRequest, SignupRequest, UserInfoResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _validate_email(email: str) -> str:
    normalized = email.strip().lower()
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    if not re.match(pattern, normalized):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email address")
    return normalized


def _validate_password(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password must be at least 8 characters")


def _user_info(user: User) -> UserInfoResponse:
    return UserInfoResponse(
        id=user.id,
        email=user.email,
        request_limit=user.request_limit,
        requests_used=user.requests_used,
        remaining_requests=max(0, user.request_limit - user.requests_used),
    )


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
        max_age=settings.auth_token_exp_minutes * 60,
        path="/",
    )


@router.post("/signup", response_model=AuthResponse, status_code=201)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    email = _validate_email(payload.email)
    _validate_password(payload.password)

    try:
        existing = await db.execute(select(User).where(User.email == email))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash, password_salt = hash_password(payload.password)
    user = User(
        email=email,
        password_hash=password_hash,
        password_salt=password_salt,
        request_limit=settings.default_user_request_limit,
        requests_used=0,
    )
    try:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except SQLAlchemyError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc

    return AuthResponse(message="Signup successful. Please sign in.", user=_user_info(user))


@router.post("/login", response_model=AuthResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    email = _validate_email(payload.email)

    try:
        result = await db.execute(select(User).where(User.email == email))
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable") from exc
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash, user.password_salt):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(user_id=user.id, email=user.email)
    _set_auth_cookie(response, token)

    return AuthResponse(message="Login successful", user=_user_info(user))


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserInfoResponse)
async def me(current_user: User = Depends(get_current_user)):
    return _user_info(current_user)