"""Startup column migrations.

`init_db()` runs `Base.metadata.create_all`, which creates missing *tables*
but never adds columns to existing ones. This module adds the columns that
newer code expects to tables created by older deployments. Idempotent and
dialect-aware; runs on every boot before create_all-created tables are used.
"""

from __future__ import annotations

import logging
import re

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
    # 009: reviews can be written by a Luna install with no marketplace account.
    ("reviews", "luna_install_id", "VARCHAR"),
    ("reviews", "author_display", "VARCHAR"),
    ("reviews", "verified_install", "BOOLEAN DEFAULT FALSE"),
]


async def run_migrations(conn: AsyncConnection) -> None:
    dialect = conn.dialect.name
    if dialect == "sqlite":
        await _run_sqlite(conn)
    else:
        await _run_postgres(conn)
        await _run_postgres_constraints(conn)
    await _run_data_fixups(conn)


# 009: a review may now be authored by a Luna install with no marketplace
# account, so reviews.user_id has to accept NULL. Only Postgres needs the
# change — SQLite deployments are dev-only and get the current DDL from
# create_all. Idempotent: DROP NOT NULL on an already-nullable column is a
# no-op in Postgres.
async def _run_postgres_constraints(conn: AsyncConnection) -> None:
    try:
        await conn.execute(text("ALTER TABLE reviews ALTER COLUMN user_id DROP NOT NULL"))
    except Exception:  # table absent on first boot
        return


# 009: plugins carry no license and no repository link. The columns stay (see
# models/db.py) but the values must go, including the "Source: <git url>" line
# older readmes were published with. Idempotent — reruns are no-ops.
async def _run_data_fixups(conn: AsyncConnection) -> None:
    try:
        res = await conn.execute(text(
            "UPDATE plugins SET source_url = NULL WHERE source_url IS NOT NULL"
        ))
        cleared = res.rowcount or 0
    except Exception:  # column already dropped, or table absent on first boot
        return
    rows = (await conn.execute(text(
        "SELECT id, readme FROM plugins WHERE readme LIKE '%http%'"
    ))).fetchall()
    stripped = 0
    for pid, readme in rows:
        cleaned = _strip_source_lines(readme or "")
        if cleaned != readme:
            await conn.execute(
                text("UPDATE plugins SET readme = :r WHERE id = :i"), {"r": cleaned, "i": pid}
            )
            stripped += 1
    if cleared or stripped:
        log.info("migrations: cleared %d source_url, stripped %d readme(s)", cleared, stripped)


_SOURCE_LINE = re.compile(r"^.*\bsource:\s*https?://\S*\n?", re.IGNORECASE | re.MULTILINE)


def _strip_source_lines(readme: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", _SOURCE_LINE.sub("", readme))


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
