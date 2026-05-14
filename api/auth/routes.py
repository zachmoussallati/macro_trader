"""Auth routes: register, login, refresh, me."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from api.auth.jwt import (
    create_access_token,
    create_refresh_token,
    hash_password,
    verify_password,
    verify_refresh_token,
)
from api.auth.models import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenPair,
    UserOut,
)
from api.deps import CurrentUserDep, SessionDep, SettingsDep
from macro_trader.db.models.auth import RefreshTokenRow, User, UserRole
from macro_trader.utils.dates import utcnow

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, session: SessionDep) -> User:
    existing = session.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.USER,
        is_active=True,
        is_superuser=False,
        created_at=utcnow(),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _issue_token_pair(
    user: User, session: SessionDep, settings: SettingsDep, *, user_agent: str = ""
) -> TokenPair:
    access = create_access_token(str(user.id), settings, role=user.role)
    raw_refresh, hashed_refresh = create_refresh_token()
    expires_at = utcnow() + timedelta(days=settings.auth.refresh_token_expire_days)
    session.add(
        RefreshTokenRow(
            user_id=user.id,
            token_hash=hashed_refresh,
            issued_at=utcnow(),
            expires_at=expires_at,
            revoked=False,
            user_agent=user_agent,
        )
    )
    session.commit()
    return TokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.auth.access_token_expire_minutes * 60,
    )


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    user = session.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")
    return _issue_token_pair(user, session, settings)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, session: SessionDep, settings: SettingsDep) -> TokenPair:
    candidates = session.scalars(
        select(RefreshTokenRow).where(RefreshTokenRow.revoked.is_(False))
    ).all()
    matched: RefreshTokenRow | None = None
    for cand in candidates:
        if verify_refresh_token(payload.refresh_token, cand.token_hash):
            matched = cand
            break
    if matched is None or matched.expires_at < utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )
    user = session.get(User, matched.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    matched.revoked = True
    session.flush()
    return _issue_token_pair(user, session, settings)


@router.get("/me", response_model=UserOut)
def me(current_user: CurrentUserDep) -> User:
    return current_user
