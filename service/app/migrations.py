"""Startup column migrations.

`init_db()` runs `Base.metadata.create_all`, which creates missing *tables*
but never adds columns to existing ones. This module adds the columns that
newer code expects to tables created by older deployments. Idempotent and
dialect-aware; runs on every boot before create_all-created tables are used.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

log = logging.getLogger("marketplace.migrations")

# (table, column, SQL type + default clause). Types must be valid in both
# SQLite and Postgres. New tables are handled by create_all, not listed here.
_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "certified_at", "INTEGER"),
    ("marketplaces", "curation", "JSON"),
    ("plugins", "category", "VARCHAR"),
    ("plugins", "rating_average", "FLOAT DEFAULT 0"),
    ("plugins", "rating_count", "INTEGER DEFAULT 0"),
]


async def run_migrations(conn: AsyncConnection) -> None:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        await _run_sqlite(conn)
    else:
        await _run_postgres(conn)


async def _run_postgres(conn: AsyncConnection) -> None:
    for table, column, ddl in _COLUMNS:
        exists = await conn.execute(
            text("SELECT to_regclass(:t)"), {"t": table}
        )
        if exists.scalar() is None:
            continue  # table doesn't exist yet; create_all will build it complete
        await conn.execute(
            text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}')
        )
        log.info("migrations: ensured %s.%s", table, column)


async def _run_sqlite(conn: AsyncConnection) -> None:
    for table, column, ddl in _COLUMNS:
        rows = await conn.execute(text(f"PRAGMA table_info({table})"))
        cols = {r[1] for r in rows.fetchall()}
        if not cols:
            continue  # table doesn't exist yet
        if column not in cols:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            log.info("migrations: added %s.%s", table, column)
