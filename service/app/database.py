"""Database session and initialization."""

from __future__ import annotations

import os
import ssl as ssl_module
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models.db import Base


def get_database_url() -> str:
    """Resolve database URL — PostgreSQL in prod, SQLite locally."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url
    db_path = Path(__file__).parent.parent / "data" / "marketplace.db"
    return f"sqlite+aiosqlite:///{db_path}"


DB_URL = get_database_url()

_engine_kwargs: dict = {"echo": False}
if "asyncpg" in DB_URL and ".render.com" in DB_URL:
    _ssl_ctx = ssl_module.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl_module.CERT_NONE
    _engine_kwargs["connect_args"] = {"ssl": _ssl_ctx}

engine = create_async_engine(DB_URL, **_engine_kwargs)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    if "sqlite" in DB_URL:
        Path(DB_URL.split("///")[1]).parent.mkdir(parents=True, exist_ok=True)
    from .migrations import run_migrations

    async with engine.begin() as conn:
        # Add missing columns to pre-existing tables first (create_all only
        # creates missing tables, never alters existing ones).
        await run_migrations(conn)
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with async_session() as session:
        yield session
