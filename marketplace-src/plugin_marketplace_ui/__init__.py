"""plugin-marketplace-ui — the rich Marketplace pane, shipped as a plugin.

Takes over the "marketplace" sidebar section from core's built-in degraded
list (via `replaces_sections`) and serves the full discover experience:
heroes, curated sections, categories, detail pages, ratings and reviews.
All install/upgrade actions still go through core plugin-marketplace APIs.
Reviews are the one thing this plugin owns end to end (see reviews.py): it
holds its own install identity in the vault and posts signed reviews straight
to the marketplace, so the owner writes a review in the pane instead of being
handed off to a website that doesn't know them. Disable or uninstall it and
core's simple list returns instantly.
"""

from __future__ import annotations

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SidebarSection


def _pkg_version(default: str = "1.2.2") -> str:
    """The version of record is `luna-plugin.toml` — that's what the
    marketplace publishes and what upgrades compare against. Read it here so
    the manifest can never drift from the package: a stale literal made every
    update "succeed" (new code on disk, new pane) while the running manifest
    kept reporting the old number, so the catalog offered the same update
    forever and the banner never cleared."""
    try:
        import tomllib
        from pathlib import Path

        data = tomllib.loads((Path(__file__).resolve().parent / "luna-plugin.toml").read_text("utf-8"))
        return str(data.get("version") or "").strip() or default
    except Exception:  # noqa: BLE001 — never let packaging trivia break the load
        return default


__version__ = _pkg_version()


class MarketplaceUiPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-marketplace-ui",
        shown_name="Marketplace UI",
        version=__version__,
        description="The full Marketplace experience — discover, curated picks, categories, and reviews.",
        sidebar_sections=[
            SidebarSection(id="marketplace", label="Marketplace", icon="plug", sort_order=40),
        ],
        replaces_sections=["marketplace"],
        routes_module="routes",
        icon="plug",
        # Declarative (Luna does not enforce egress): the review proxy signs
        # with credentials under this plugin's own `plugin_marketplace_ui.`
        # vault namespace and only ever calls a marketplace the owner added.
        permissions=[
            {"vault_access": True, "reason": "holds this install's review identity"},
            {"egress": "owner-configured marketplaces", "reason": "posting reviews"},
        ],
    )

    async def on_load(self, ctx: PluginContext) -> None:
        pass
