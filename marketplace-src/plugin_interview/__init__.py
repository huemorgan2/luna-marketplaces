"""plugin-interview — adaptive knowledge-elicitation interviews.

Drop-in, fully decoupled (008.001). The plugin owns *state and structure*
(coverage map, turns, brief); the agent does the research, judgment, and
conversation. Copying this folder enables it; deleting it removes it cleanly.
No core wiring file is edited to add it.
"""

from __future__ import annotations

import logging
from typing import Any

from luna_sdk import (
    LunaPlugin,
    PluginContext,
    PluginManifest,
    SidebarSection,
)

from .models import ALL_TABLES
from .prompts import CAPABILITY_NOTE
from .store import InterviewStore
from .tools import register_tools

log = logging.getLogger("plugin-interview")


class InterviewPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-interview",
        version="0.1.1",
        description="Adaptive knowledge-elicitation interviews → structured briefs.",
        category="global",
        db_tables=[t.name for t in ALL_TABLES],
        routes_module="routes",
        sidebar_sections=[
            SidebarSection(
                id="interview",
                label="Interviews",
                icon="messages-square",
                sort_order=45,
            ),
        ],
    )

    def __init__(self) -> None:
        self._store: InterviewStore | None = None

    async def on_load(self, ctx: PluginContext) -> None:
        async with ctx.engine.begin() as conn:
            for table in ALL_TABLES:
                await conn.run_sync(table.create, checkfirst=True)
        self._store = InterviewStore(ctx.db_session_factory)
        register_tools(ctx, self._store)
        log.info("plugin-interview loaded (tools=9, tables=%d)", len(ALL_TABLES))

    async def prompt_sections(self) -> list[Any]:
        return [CAPABILITY_NOTE]
