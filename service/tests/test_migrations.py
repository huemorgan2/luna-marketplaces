"""Startup migration adds new columns to a pre-existing (old-schema) database."""

import tempfile
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.migrations import run_migrations


async def test_sqlite_columns_added_idempotently():
    tmp = Path(tempfile.mkdtemp(prefix="mig-test-")) / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}")

    async with engine.begin() as conn:
        # old-schema tables, as created by deployments before plan 005
        await conn.execute(text(
            "CREATE TABLE users (id VARCHAR PRIMARY KEY, email VARCHAR, username VARCHAR, "
            "password_hash VARCHAR, created_at INTEGER, is_active BOOLEAN)"))
        await conn.execute(text(
            "CREATE TABLE plugins (id VARCHAR PRIMARY KEY, name VARCHAR, download_count INTEGER)"))
        await conn.execute(text(
            "CREATE TABLE marketplaces (id VARCHAR PRIMARY KEY, slug VARCHAR)"))
        await conn.execute(text("INSERT INTO users VALUES ('u1','a@b.c','ab','x',0,1)"))

    async with engine.begin() as conn:
        await run_migrations(conn)
    async with engine.begin() as conn:  # second run must be a no-op
        await run_migrations(conn)

    async with engine.begin() as conn:
        users_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()}
        plugins_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(plugins)"))).fetchall()}
        mp_cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(marketplaces)"))).fetchall()}
        assert "certified_at" in users_cols
        assert {"category", "rating_average", "rating_count"} <= plugins_cols
        assert "curation" in mp_cols
        # existing rows intact, new column NULL
        row = (await conn.execute(text("SELECT email, certified_at FROM users"))).one()
        assert row == ("a@b.c", None)

    await engine.dispose()


async def test_migration_skips_missing_tables():
    tmp = Path(tempfile.mkdtemp(prefix="mig-test-")) / "empty.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp}")
    async with engine.begin() as conn:
        await run_migrations(conn)  # nothing exists; must not raise
    await engine.dispose()
