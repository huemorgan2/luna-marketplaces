"""plugin-funnelfighters — Marketing intelligence via FunnelFighters + 4 Ducks methodology.

Registers read-only tools for querying the FunnelFighters API and a 4 Ducks
methodology skill the agent can load on demand. A connectors-category plugin
that ships installed-but-OFF (``default_enabled=False``): it shows in the
Plugins list toggled off. Turning it on exposes its tools/skill to the agent
and surfaces a "FunnelFighters" tab in Settings where the user pastes their
API key + org ID. Tools stay registered before connection and return a
friendly "not connected" result until credentials are saved.
"""

from __future__ import annotations

import logging

from luna_sdk import (
    CredentialSlot,
    LunaPlugin,
    PluginContext,
    PluginManifest,
    SettingsTab,
    SkillDef,
)

from .client import FFClient
from .config import FF_DEFAULT_BASE_URL, FF_VAULT_KEY_API, FF_VAULT_KEY_ORG
from .knowledge import FOUR_DUCKS_KNOWLEDGE
from .state import get_client, set_client
from .tools import build_tools

log = logging.getLogger("plugin-funnelfighters")


class FunnelFightersPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-funnelfighters",
        version="0.2.1",
        description="Marketing intelligence via FunnelFighters + 4 Ducks methodology",
        category="connectors",
        depends_on=["plugin-vault"],
        # Discovered and loaded at boot so it appears in the Plugins list, but
        # seeded OFF — the user enables it and adds credentials in Settings.
        auto_load=True,
        default_enabled=False,
        routes_module="routes",
        settings_tabs=[
            SettingsTab(
                id="funnelfighters",
                label="FunnelFighters",
                icon="target",
                sort_order=75,
                iframe_src="/api/p/plugin-funnelfighters/ui/settings/",
            ),
        ],
        interfaces={"webui": "interface/webui"},
    )

    @property
    def active(self) -> bool:
        return get_client() is not None

    def credential_slots(self) -> list[CredentialSlot]:
        return [
            CredentialSlot(
                slug="funnelfighters",
                credential_name=FF_VAULT_KEY_API,
                env_key_var="LUNA_FUNNELFIGHTERS_API_KEY",
                owner=self.manifest.name,
            ),
            CredentialSlot(
                slug="funnelfighters",
                credential_name=FF_VAULT_KEY_ORG,
                env_key_var="LUNA_FUNNELFIGHTERS_ORG_ID",
                owner=self.manifest.name,
            ),
        ]

    def _get_client(self) -> FFClient:
        client = get_client()
        if client is None:
            raise RuntimeError(
                "FunnelFighters not connected — add the API key and org ID in Settings."
            )
        return client

    async def on_load(self, ctx: PluginContext) -> None:
        # Tools + skill always register so the agent can see them the moment the
        # plugin is toggled on; the runtime filter hides them while it's off.
        # The client stays None until credentials are present (here at boot or
        # later via the Settings connect flow), and tools report "not connected".
        set_client(None)
        for tool_def, handler in build_tools(self._get_client):
            ctx.tool_registry.register(self.manifest.name, tool_def, handler)

        if ctx.skill_registry is not None:
            ctx.skill_registry.register(
                self.manifest.name,
                SkillDef(
                    name="four-ducks",
                    description=(
                        "marketing funnel analysis methodology — load before "
                        "analyzing ads, funnels, or campaign performance"
                    ),
                    body=FOUR_DUCKS_KNOWLEDGE,
                ),
            )

        await self._connect_from_vault(ctx)
        log.info("plugin-funnelfighters loaded (tools=15, connected=%s)", self.active)

    async def _connect_from_vault(self, ctx: PluginContext) -> None:
        """Build the client if both credentials are already in the vault."""
        vault = ctx.vault
        if vault is None:
            log.warning("Vault not available; plugin-funnelfighters inactive")
            return

        try:
            api_key = (await vault.get_credential(FF_VAULT_KEY_API)).value
        except KeyError:
            api_key = None
        try:
            org_id = (await vault.get_credential(FF_VAULT_KEY_ORG)).value
        except KeyError:
            org_id = None

        if not api_key or not org_id:
            log.info("API key or org_id not in vault; not connected")
            return

        set_client(FFClient(base_url=FF_DEFAULT_BASE_URL, api_key=api_key, org_id=org_id))

    async def on_unload(self) -> None:
        client = get_client()
        if client is not None:
            await client.close()
            set_client(None)
