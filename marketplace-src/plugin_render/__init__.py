"""plugin-render — Render.com service management.

Connects Luna to the Render API for deploy management, service control,
environment variables, and live logs. Requires an API key stored in vault.
"""

from __future__ import annotations

import logging
from typing import Any

from luna_sdk import (
    LunaPlugin,
    PluginContext,
    PluginManifest,
    SettingsTab,
    SkillDef,
    ToolDef,
)

from .client import RenderClient
from .state import get_client, set_client

log = logging.getLogger("plugin-render")

VAULT_KEY = "plugin_render.api_key"


class RenderPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-render",
        version="0.1.1",
        description="Render.com service management — deploys, env vars, logs, and lifecycle control.",
        category="connectors",
        depends_on=["plugin-vault"],
        routes_module="routes",
        settings_tabs=[
            SettingsTab(
                id="render",
                label="Render",
                icon="cloud",
                sort_order=70,
                iframe_src="/api/p/plugin-render/ui/settings/",
            ),
        ],
        interfaces={"webui": "interface/webui"},
    )

    async def on_load(self, ctx: PluginContext) -> None:
        set_client(None)
        vault = ctx.vault
        if vault is None:
            log.warning("Vault provider not available; plugin-render inactive")
            return

        api_key: str | None = None
        try:
            cred = await vault.get_credential(VAULT_KEY)
            api_key = cred.value
        except KeyError:
            pass

        if api_key:
            set_client(RenderClient(api_key))

        self._register_tools(ctx)
        self._register_skills(ctx)

        log.info("plugin-render loaded (tools=12, connected=%s)", get_client() is not None)

    async def on_unload(self) -> None:
        client = get_client()
        if client is not None:
            await client.close()
            set_client(None)

    def _get_client(self) -> RenderClient:
        client = get_client()
        if client is None:
            raise RuntimeError("Render API key not configured. Add it in Settings > Render.")
        return client

    # ── tools ─────────────────────────────────────────────────

    def _register_tools(self, ctx: PluginContext) -> None:
        plugin = self.manifest.name

        def _reg(tool_def: ToolDef, handler) -> None:
            ctx.tool_registry.register(plugin, tool_def, handler, skill_gated=True)

        # --- services ---

        async def _list_services() -> dict[str, Any]:
            return {"services": await self._get_client().list_services()}

        _reg(
            ToolDef(
                name="render_list_services",
                description="List all Render services.",
                parameters={"type": "object", "properties": {}},
            ),
            _list_services,
        )

        async def _get_service(service_id: str) -> dict[str, Any]:
            return await self._get_client().get_service(service_id)

        _reg(
            ToolDef(
                name="render_get_service",
                description="Get details of a specific Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
            ),
            _get_service,
        )

        async def _restart_service(service_id: str) -> dict[str, Any]:
            return await self._get_client().restart_service(service_id)

        _reg(
            ToolDef(
                name="render_restart_service",
                description="Restart a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
                risk_level="medium",
            ),
            _restart_service,
        )

        async def _suspend_service(service_id: str) -> dict[str, Any]:
            return await self._get_client().suspend_service(service_id)

        _reg(
            ToolDef(
                name="render_suspend_service",
                description="Suspend a Render service (stops it).",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
                risk_level="high",
            ),
            _suspend_service,
        )

        async def _resume_service(service_id: str) -> dict[str, Any]:
            return await self._get_client().resume_service(service_id)

        _reg(
            ToolDef(
                name="render_resume_service",
                description="Resume a suspended Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
            ),
            _resume_service,
        )

        async def _get_live_logs(service_id: str) -> dict[str, Any]:
            return {"logs": await self._get_client().get_live_logs(service_id)}

        _reg(
            ToolDef(
                name="render_get_live_logs",
                description="Get recent logs from a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
            ),
            _get_live_logs,
        )

        # --- deploys ---

        async def _trigger_deploy(
            service_id: str, clear_cache: bool = False,
        ) -> dict[str, Any]:
            return await self._get_client().trigger_deploy(
                service_id, clear_cache=clear_cache,
            )

        _reg(
            ToolDef(
                name="render_trigger_deploy",
                description="Trigger a new deploy for a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                        "clear_cache": {"type": "boolean", "description": "Clear build cache.", "default": False},
                    },
                    "required": ["service_id"],
                },
                risk_level="medium",
            ),
            _trigger_deploy,
        )

        async def _list_deploys(service_id: str) -> dict[str, Any]:
            return {"deploys": await self._get_client().list_deploys(service_id)}

        _reg(
            ToolDef(
                name="render_list_deploys",
                description="List recent deploys for a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
            ),
            _list_deploys,
        )

        async def _get_deploy(service_id: str, deploy_id: str) -> dict[str, Any]:
            return await self._get_client().get_deploy(service_id, deploy_id)

        _reg(
            ToolDef(
                name="render_get_deploy",
                description="Get details of a specific deploy.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                        "deploy_id": {"type": "string", "description": "The deploy ID."},
                    },
                    "required": ["service_id", "deploy_id"],
                },
            ),
            _get_deploy,
        )

        # --- env vars ---

        async def _list_env_vars(service_id: str) -> dict[str, Any]:
            return {"env_vars": await self._get_client().list_env_vars(service_id)}

        _reg(
            ToolDef(
                name="render_list_env_vars",
                description="List environment variables for a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                    },
                    "required": ["service_id"],
                },
            ),
            _list_env_vars,
        )

        async def _set_env_var(
            service_id: str, key: str, value: str,
        ) -> dict[str, Any]:
            return await self._get_client().set_env_var(service_id, key, value)

        _reg(
            ToolDef(
                name="render_set_env_var",
                description="Set an environment variable on a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                        "key": {"type": "string", "description": "Env var name."},
                        "value": {"type": "string", "description": "Env var value."},
                    },
                    "required": ["service_id", "key", "value"],
                },
                risk_level="high",
                sensitive_args=["value"],
            ),
            _set_env_var,
        )

        async def _delete_env_var(service_id: str, key: str) -> dict[str, Any]:
            await self._get_client().delete_env_var(service_id, key)
            return {"deleted": True, "key": key}

        _reg(
            ToolDef(
                name="render_delete_env_var",
                description="Delete an environment variable from a Render service.",
                parameters={
                    "type": "object",
                    "properties": {
                        "service_id": {"type": "string", "description": "The Render service ID."},
                        "key": {"type": "string", "description": "Env var name to delete."},
                    },
                    "required": ["service_id", "key"],
                },
                risk_level="high",
            ),
            _delete_env_var,
        )

    # ── skills ────────────────────────────────────────────────

    def _register_skills(self, ctx: PluginContext) -> None:
        if ctx.skill_registry is None:
            return

        plugin = self.manifest.name

        ctx.skill_registry.register(
            plugin,
            SkillDef(
                name="render-services",
                description=(
                    "Render service management — list, inspect, restart, "
                    "suspend, resume services, and read live logs"
                ),
                body=(
                    "You now have access to Render service management tools. "
                    "Use render_list_services to discover services, "
                    "render_get_service for details, render_restart_service / "
                    "render_suspend_service / render_resume_service for lifecycle, "
                    "and render_get_live_logs for recent output."
                ),
                tools=[
                    "render_list_services",
                    "render_get_service",
                    "render_restart_service",
                    "render_suspend_service",
                    "render_resume_service",
                    "render_get_live_logs",
                ],
            ),
        )

        ctx.skill_registry.register(
            plugin,
            SkillDef(
                name="render-deploys",
                description="Render deploy management — trigger, list, and inspect deploys",
                body=(
                    "You now have access to Render deploy tools. "
                    "Use render_trigger_deploy to start a new deploy, "
                    "render_list_deploys to see recent deploys, and "
                    "render_get_deploy for deploy details."
                ),
                tools=[
                    "render_trigger_deploy",
                    "render_list_deploys",
                    "render_get_deploy",
                ],
            ),
        )

        ctx.skill_registry.register(
            plugin,
            SkillDef(
                name="render-env",
                description="Render environment variable management — list, set, and delete env vars",
                body=(
                    "You now have access to Render environment variable tools. "
                    "Use render_list_env_vars to see current vars, "
                    "render_set_env_var to create or update a var, and "
                    "render_delete_env_var to remove one."
                ),
                tools=[
                    "render_set_env_var",
                    "render_list_env_vars",
                    "render_delete_env_var",
                ],
            ),
        )
