"""plugin-recall — verbatim conversation retrieval for the agent (008.002).

Drop-in, fully decoupled. Gives the agent a way back to the EXACT earlier words
of a conversation (current or, on request, the owner's other conversations) so a
condensed recap is never the only thing it can see. Read-only; ungated
(auto_approve) like other read tools. `depends_on == []`.
"""

from __future__ import annotations

import logging

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, ToolDef

from .tools import make_recall_handler

log = logging.getLogger("plugin-recall")

_CAPABILITY_NOTE = (
    "Verbatim recall: you can fetch the EXACT earlier messages of a conversation "
    "with the `recall_conversation` tool (keyword search or range fetch; scope "
    "'current' by default, 'all' for the owner's other conversations). Use it "
    "when you need the precise earlier wording you don't currently have in "
    "context — e.g. \"what did I originally ask you to do?\" — instead of guessing "
    "or relying on a condensed summary."
)


class RecallPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-recall",
        version="0.1.1",
        description="Verbatim conversation retrieval (recall_conversation).",
        category="global",
    )

    async def on_load(self, ctx: PluginContext) -> None:
        handler = make_recall_handler(ctx)
        ctx.tool_registry.register(
            "plugin-recall",
            ToolDef(
                name="recall_conversation",
                description=(
                    "Retrieve VERBATIM earlier messages from a conversation. Use when "
                    "you need exact earlier wording you don't have in context (e.g. the "
                    "user's original request). Params: query (keyword/substring), scope "
                    "('current' default | 'all' = owner's other conversations too), "
                    "conversation_id (target a specific one), offset/limit (range fetch, "
                    "e.g. the earliest 20), order ('asc' default | 'desc'). Returns a "
                    "compact list with role, author, time, and content."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keyword / substring to match in message content."},
                        "scope": {"type": "string", "enum": ["current", "all"], "default": "current"},
                        "conversation_id": {"type": "string"},
                        "offset": {"type": "integer", "default": 0},
                        "limit": {"type": "integer", "default": 20},
                        "order": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
                    },
                },
            ),
            handler,
        )
        log.info("plugin-recall loaded (tools=1)")

    async def prompt_sections(self) -> list[str]:
        return [_CAPABILITY_NOTE]
