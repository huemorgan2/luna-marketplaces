"""plugin-db — simple structured data tables with a monday-style board UI.

User-defined tables with typed columns (text, number, status, date, checkbox,
email, phone, address, currency, percent), up to 10,000 rows each, stored in
the agent's own Postgres as data (metadata tables + one JSONB rows table — no
runtime DDL). Full agent CRUD + search tools, and a board pane with tabs,
search, filters, in-place editing, and remembered column widths.

Authored against `luna_sdk` only.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SidebarSection

from .models import ALL_TABLES
from .store import DbStore
from .tools import register_tools

__version__ = "0.1.1"

log = logging.getLogger("plugin-db")


class DbPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-db",
        shown_name="DB",
        icon="table",
        version=__version__,
        description=(
            "Structured data tables (typed columns, 10k rows each) with a "
            "monday-style board: tabs, search, filters, in-place editing."
        ),
        category="global",
        license="MIT",
        db_tables=[t.name for t in ALL_TABLES],
        routes_module="routes",
        sidebar_sections=[
            SidebarSection(id="db", label="DB", icon="table", sort_order=47),
        ],
    )

    def __init__(self) -> None:
        self._store: DbStore | None = None

    async def on_load(self, ctx: PluginContext) -> None:
        async with ctx.engine.begin() as conn:
            for table in ALL_TABLES:
                await conn.run_sync(table.create, checkfirst=True)
            # Existing 0.1.0 installations predate the reservation column.
            # ADD COLUMN with its bound preserves every old table/row and is
            # transactional on both supported engines.
            from sqlalchemy import inspect
            columns = await conn.run_sync(lambda sync: {c["name"] for c in inspect(sync).get_columns("db_tables")})
            if "row_reservation" not in columns:
                await conn.execute(text(
                    "ALTER TABLE db_tables ADD COLUMN row_reservation INTEGER NOT NULL "
                    "DEFAULT 0 CHECK (row_reservation <= 10000)"
                ))
            if conn.dialect.name == "postgresql":
                # headroom for JSONB containment filters; SQLite dev runs
                # without it (a 10k-row table scan is milliseconds anyway)
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_db_rows_data_gin "
                        "ON db_rows USING GIN (data jsonb_path_ops)"
                    )
                )
        self._store = DbStore(ctx.db_session_factory)

        async def _on_change(evt: dict) -> None:
            # live-update feed for the board pane (SSE /api/events?topics=db.*)
            await ctx.events.emit("db.updated", evt)

        self._store.on_change = _on_change
        register_tools(ctx, self._store)
        log.info("plugin-db loaded (tools=14, tables=%d)", len(ALL_TABLES))
