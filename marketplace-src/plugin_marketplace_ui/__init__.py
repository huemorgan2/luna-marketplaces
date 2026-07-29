"""plugin-marketplace-ui — the rich Marketplace pane, shipped as a plugin.

Takes over the "marketplace" sidebar section from core's built-in degraded
list (via `replaces_sections`) and serves the full discover experience:
heroes, curated sections, categories, detail pages, ratings and reviews.
All install/upgrade actions still go through core plugin-marketplace APIs —
this plugin is UI only. Disable or uninstall it and core's simple list
returns instantly.
"""

from __future__ import annotations

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SidebarSection

__version__ = "1.0.0"


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
    )

    async def on_load(self, ctx: PluginContext) -> None:
        pass
