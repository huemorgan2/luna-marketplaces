"""plugin-chat-ui — Luna's product chat, shipped as a plugin.

Takes over the built-in "chat" panel (via `replaces_sections`) with the full
rich chat: markdown + streaming, inline approval cards, vault secret forms,
chart embeds, playbook styling, context meter, model picker, debug mode.

Unlike iframe panes, this plugin's UI mounts NATIVELY in the shell's React
tree: `ui/chat.js` is an IIFE bundle compiled with react/react-dom as
externals resolved to the shell's `window.LunaReact*` globals, and it
registers `window.LunaChatUI.ChatPanel`. The shell loads it at runtime and
renders it exactly where its own chat panel would go. If the bundle fails to
load or crashes, core's BasicChat (a tiny debug chat) takes over — disabling
or uninstalling this plugin does the same.
"""

from __future__ import annotations

from luna_sdk import LunaPlugin, PluginContext, PluginManifest


def _pkg_version(default: str = "0.1.0") -> str:
    """Version of record is `luna-plugin.toml` (what the marketplace
    publishes and what upgrades compare against) — read it so the manifest
    can never drift from the package."""
    try:
        import tomllib
        from pathlib import Path

        data = tomllib.loads((Path(__file__).resolve().parent / "luna-plugin.toml").read_text("utf-8"))
        return str(data.get("version") or "").strip() or default
    except Exception:  # noqa: BLE001 — never let packaging trivia break the load
        return default


__version__ = _pkg_version()


class ChatUiPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-chat-ui",
        shown_name="Chat UI",
        version=__version__,
        description="Luna's full chat experience — rich messages, inline approvals, secret forms, embeds, and debug mode.",
        # Take over the built-in chat panel. No sidebar_sections: the builtin
        # "Chat" nav item stays; only the mounted panel swaps.
        replaces_sections=["chat"],
        routes_module="routes",
        icon="message-square",
    )

    async def on_load(self, ctx: PluginContext) -> None:
        pass
