"""Authentication utilities — JWT + password hashing."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import hashlib

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models.db import PublishToken, User

import os

SECRET_KEY = os.environ.get("JWT_SECRET", "luna-marketplaces-dev-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 72

security = HTTPBearer(auto_error=False)

# Global editor allow list: emails permitted to publish to ANY catalog (including
# `official`), independent of org/account membership. Set via the GLOBAL_EDITORS
# env var as a comma-separated list of emails. Used for catalogs (like official)
# that have no real account behind them.
GLOBAL_EDITORS: set[str] = {
    e.strip().lower()
    for e in os.environ.get("GLOBAL_EDITORS", "").split(",")
    if e.strip()
}


def is_global_editor(user: User) -> bool:
    """True if the user is on the global editor allow list (by email)."""
    return bool(user.email) and user.email.lower() in GLOBAL_EDITORS


# Google OAuth (server-side authorization-code flow). All optional — if unset,
# the Google sign-in endpoints return 503 and the rest of auth keeps working.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# Canonical public origin used to build the OAuth redirect URI so it matches
# exactly what is registered in Google Cloud. e.g. https://luna-marketplaces.onrender.com
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def google_oauth_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def hash_password(password: str) -> str:
    return hashlib.sha256(f"luna-mp-salt:{password}".encode()).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# Publish tokens: long-lived credentials issued from the UI, scoped to one
# marketplace. Distinguished from session JWTs by this prefix.
PUBLISH_TOKEN_PREFIX = "lmp_"


def hash_publish_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_publish_token() -> str:
    import secrets

    return PUBLISH_TOKEN_PREFIX + secrets.token_urlsafe(32)


async def _resolve_publish_token(token: str, db: AsyncSession) -> User:
    """Resolve a `lmp_` bearer to its owning user.

    The token's marketplace scope is attached to the returned user object as
    `_publish_token_mp_id`, enforced by the publisher gate. Session JWTs never
    set the attribute, so they keep full account access.
    """
    result = await db.execute(
        select(PublishToken).where(
            PublishToken.token_hash == hash_publish_token(token),
            PublishToken.revoked == False,  # noqa: E712
        )
    )
    pt = result.scalar_one_or_none()
    if not pt:
        raise HTTPException(status_code=401, detail="Invalid or revoked publish token")

    user = await db.get(User, pt.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found")

    pt.last_used_at = int(time.time())
    await db.commit()

    user._publish_token_mp_id = pt.marketplace_id  # type: ignore[attr-defined]
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    if credentials.credentials.startswith(PUBLISH_TOKEN_PREFIX):
        return await _resolve_publish_token(credentials.credentials, db)

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
