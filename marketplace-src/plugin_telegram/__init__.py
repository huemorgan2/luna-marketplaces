"""Luna connector for Telegram through luna-tg-gateway."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SettingsTab, ToolDef

from . import client, db

log = logging.getLogger("plugin-telegram")

__version__ = "0.2.0"
TG_TOOL_NAMES = [
    "tg_send",
    "tg_send_media",
    "tg_react",
    "tg_list_chats",
    "tg_status",
]

_CAPABILITY_NOTE = (
    "Telegram: you are connected as a Bot API bot. You can send text with "
    "`tg_send`, deliver native media with `tg_send_media`, react with `tg_react`, "
    "list known chats with `tg_list_chats`, and inspect gateway/webhook health "
    "with `tg_status`. Telegram chat IDs are numeric; known chat names may be used "
    "when they resolve uniquely."
)


class TelegramPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-telegram",
        shown_name="Telegram",
        version=__version__,
        description="Connect Luna to Telegram through an official Bot API gateway.",
        category="connectors",
        depends_on=["plugin-vault"],
        routes_module="routes",
        icon="send",
        settings_tabs=[
            SettingsTab(
                id="telegram",
                label="Telegram",
                icon="send",
                sort_order=67,
                iframe_src="/api/p/plugin-telegram/ui/settings/",
            )
        ],
    )

    async def on_load(self, ctx: PluginContext) -> None:
        await db.create_tables(ctx.engine)
        self._register_tools(ctx)
        log.info("plugin-telegram loaded (tools=%d)", len(TG_TOOL_NAMES))

    async def prompt_sections(self) -> list[str]:
        return [_CAPABILITY_NOTE]

    def _register_tools(self, ctx: PluginContext) -> None:
        plugin_name = self.manifest.name

        def register(tool: ToolDef, handler) -> None:
            ctx.tool_registry.register(plugin_name, tool, handler)

        async def tg_send(
            chat: str, text: str, reply_to: int | None = None
        ) -> dict[str, Any]:
            account = await client.account_id(ctx) or "default"
            resolved = await db.resolve_chat(ctx.engine, chat, account=account)
            response = await client.send_message(
                ctx, resolved["chat_id"], text, reply_to=reply_to
            )
            await _record_outbound(
                ctx,
                resolved,
                response,
                kind="text",
                body=text,
                reply_to=reply_to,
                account=account,
            )
            return {**response, "resolved_chat_id": resolved["chat_id"]}

        register(
            ToolDef(
                name="tg_send",
                description=(
                    "Send Telegram text to a numeric chat ID or a uniquely named "
                    "known chat. This is an external side effect."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "chat": {"type": "string", "description": "Chat ID or known name."},
                        "text": {"type": "string"},
                        "reply_to": {"type": "integer"},
                    },
                    "required": ["chat", "text"],
                },
                policy="prompt_always",
                risk_level="medium",
                sensitive_args=["text"],
            ),
            tg_send,
        )

        async def tg_send_media(
            chat: str,
            media_type: str,
            source: str,
            caption: str | None = None,
            reply_to: int | None = None,
        ) -> dict[str, Any]:
            account = await client.account_id(ctx) or "default"
            resolved = await db.resolve_chat(ctx.engine, chat, account=account)
            response = await client.send_media(
                ctx,
                resolved["chat_id"],
                media_type,
                source,
                caption=caption,
                reply_to=reply_to,
            )
            await _record_outbound(
                ctx,
                resolved,
                response,
                kind=media_type,
                body=caption,
                reply_to=reply_to,
                media={"type": media_type, "source": source},
                account=account,
            )
            return {**response, "resolved_chat_id": resolved["chat_id"]}

        register(
            ToolDef(
                name="tg_send_media",
                description=(
                    "Send photo, animation, video, voice, audio, document, or "
                    "sticker media through Telegram."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "chat": {"type": "string"},
                        "media_type": {
                            "type": "string",
                            "enum": [
                                "photo", "animation", "video", "voice", "audio",
                                "document", "sticker",
                            ],
                        },
                        "source": {
                            "type": "string",
                            "description": "Telegram file_id or gateway-readable URL/ref.",
                        },
                        "caption": {"type": "string"},
                        "reply_to": {"type": "integer"},
                    },
                    "required": ["chat", "media_type", "source"],
                },
                policy="prompt_always",
                risk_level="medium",
                sensitive_args=["caption", "source"],
            ),
            tg_send_media,
        )

        async def tg_react(chat: str, tg_msg_id: int, emoji: str) -> dict[str, Any]:
            account = await client.account_id(ctx) or "default"
            resolved = await db.resolve_chat(ctx.engine, chat, account=account)
            response = await client.react_message(
                ctx, resolved["chat_id"], tg_msg_id, emoji
            )
            return {**response, "resolved_chat_id": resolved["chat_id"]}

        register(
            ToolDef(
                name="tg_react",
                description="React to a Telegram message with an emoji.",
                parameters={
                    "type": "object",
                    "properties": {
                        "chat": {"type": "string"},
                        "tg_msg_id": {"type": "integer"},
                        "emoji": {"type": "string"},
                    },
                    "required": ["chat", "tg_msg_id", "emoji"],
                },
                policy="prompt_always",
                risk_level="low",
            ),
            tg_react,
        )

        async def tg_list_chats(limit: int = 50) -> dict[str, Any]:
            account = await client.account_id(ctx) or "default"
            return {
                "chats": await db.list_chats(
                    ctx.engine, limit=limit, account=account
                )
            }

        register(
            ToolDef(
                name="tg_list_chats",
                description="List recent Telegram chats known from plugin history.",
                parameters={
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "default": 50}},
                },
                policy="auto_approve",
                risk_level="low",
            ),
            tg_list_chats,
        )

        async def tg_status() -> dict[str, Any]:
            try:
                return await client.health(ctx)
            except Exception as exc:  # noqa: BLE001
                return {"connected": False, "error": str(exc)}

        register(
            ToolDef(
                name="tg_status",
                description="Check Telegram bot, gateway, and webhook health.",
                parameters={"type": "object", "properties": {}},
                policy="auto_approve",
                risk_level="low",
            ),
            tg_status,
        )


async def _record_outbound(
    ctx,
    chat: dict[str, Any],
    response: dict[str, Any],
    *,
    kind: str,
    body: str | None,
    reply_to: int | None,
    media: dict[str, Any] | None = None,
    account: str = "default",
) -> None:
    message_id = client.outbound_message_id(response)
    await db.record_message(
        ctx.engine,
        account=account,
        event_type="message",
        chat_id=chat["chat_id"],
        chat_kind=chat.get("chat_kind") or "unknown",
        chat_name=chat.get("chat_name"),
        sender_id=None,
        sender_name="Luna",
        from_me=True,
        tg_update_id=None,
        tg_msg_id=message_id,
        reply_to_id=reply_to,
        ts=datetime.now(timezone.utc),
        kind=kind,
        body=body,
        media_json=media,
        raw_json=None,
    )


__all__ = ["TG_TOOL_NAMES", "TelegramPlugin", "__version__"]
