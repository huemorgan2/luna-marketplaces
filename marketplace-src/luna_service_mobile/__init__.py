"""luna-service-mobile: forwards chat activity to the luna.com.ai control plane for the iPhone feed.

luna-service plan 082. Hosted Lunas only (needs LUNA_GATEWAY_URL + LUNA_GATEWAY_TOKEN).
"""

from __future__ import annotations

import logging
import os

from luna_sdk import LunaPlugin, PluginContext, PluginManifest

from .reader import Reader
from .sender import Sender

log = logging.getLogger("luna-service-mobile")

__version__ = "0.1.0"

# Luna bus topic -> queued item kind. All subscriptions are background: a handler only enqueues.
TOPICS = {
    "message.received": "user_message",
    "message.created": "created",
    "agent.turn.started": "turn.started",
    "agent.turn.ended": "turn.ended",
    "approval.requested": "approval.requested",
    "approval.decided": "approval.decided",
    "approval.orphan_decided": "approval.decided",
    "conversation.state_changed": "conversation",
}


def _setting(ctx, key: str) -> str:
    getter = getattr(ctx, "get_env", None)
    if getter is not None:
        value = getter(key)
        if value:
            return str(value).strip()
    return (os.environ.get(key) or "").strip()


class LunaServiceMobilePlugin(LunaPlugin):
    manifest = PluginManifest(
        name="luna-service-mobile",
        shown_name="Luna mobile feed",
        version=__version__,
        description="Tells luna.com.ai when this Luna's chats change, so the Luna iPhone app has a live feed.",
        category="connectors",
        icon="smartphone",
    )

    def __init__(self) -> None:
        super().__init__()
        self._sender: Sender | None = None

    async def on_load(self, ctx: PluginContext) -> None:
        base = _setting(ctx, "LUNA_GATEWAY_URL")
        token = _setting(ctx, "LUNA_GATEWAY_TOKEN")
        if not (base and token):
            log.info("luna-service-mobile: no gateway configured (self-hosted); staying idle")
            return
        reader = Reader(ctx.db_session_factory)
        self._sender = Sender(base, token, reader.resolve)
        for topic, kind in TOPICS.items():
            ctx.events.subscribe(topic, self._handler(kind), background=True)
        log.info("luna-service-mobile loaded; forwarding chat activity")

    def _handler(self, kind: str):
        async def handle(payload: dict) -> None:
            if self._sender is not None:
                self._sender.put((kind, dict(payload or {})))
        return handle

    async def on_server_ready(self) -> None:
        # Background tasks must start here, on the server's loop (on_load runs on a boot loop).
        if self._sender is None:
            return
        self._sender.start()
        self._sender.put(("snapshot", {}))

    async def on_unload(self) -> None:
        if self._sender is not None:
            await self._sender.stop()
            self._sender = None


__all__ = ["LunaServiceMobilePlugin", "__version__"]
