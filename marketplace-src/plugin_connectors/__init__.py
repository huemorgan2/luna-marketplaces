"""plugin-connectors — one aggregator plugin, multiple providers (tools + triggers).

Connect hundreds of platforms through provider drivers (Composio first) with
zero per-app code. Apps are connected/enabled from the Connectors settings
page; each enabled app's actions are registered as skill-gated agent tools
(`{provider}__{app}__{action}`, skill `connector-{app}`), and provider trigger
events flow through `/api/p/plugin-connectors/events/{provider}` onto the Luna
event bus as `connector.{app}.{trigger_slug}` for any listener.

006.713: triggers are published through the neutral luna.triggers registry
(TriggerSource protocol) — this plugin never names its consumers.

Plans: plans/006-tasks-engine/006.710-connectors/PLAN.md
       plans/006-tasks-engine/006.713-decoupled-triggers/PLAN.md
"""

from __future__ import annotations

import json
import logging
from typing import Any

from luna_sdk import (
    CredentialSlot,
    LunaPlugin,
    PluginContext,
    PluginManifest,
    SettingsTab,
    SkillDef,
    ToolDef,
)

from .protocol import AppInfo, ConnectorProvider, ConnectorProviderError, ProviderToolDef

log = logging.getLogger("plugin-connectors")

# 007.001: canonical credential name per the {slug}_api_key convention.
# The legacy name predates the convention and is still read as fallback.
VAULT_COMPOSIO_CANONICAL = "composio_api_key"
VAULT_COMPOSIO_KEY = "plugin_connectors.composio.api_key"  # legacy
VAULT_STATE_KEY = "plugin_connectors.state"

# GitHub alone has 846 actions — cap what we expose per app to keep the
# agent's unlocked toolset sane. Curation beats completeness.
MAX_TOOLS_PER_APP = 60

# Identity probes — a cheap read-only provider tool per app whose response
# reveals WHICH account is connected (email / username). The raw response is
# reduced to a single display label via the candidate dot-paths below; nothing
# else from the probe output ever leaves the plugin. Apps without an entry
# simply show no identity line.
_IDENTITY_PROBES: dict[str, tuple[str, dict[str, Any], tuple[str, ...]]] = {
    "gmail": ("GMAIL_GET_PROFILE", {}, ("response_data.emailAddress",)),
    "googlecalendar": (
        "GOOGLECALENDAR_GET_CALENDAR",
        {"calendar_id": "primary"},
        ("calendar_data.id", "calendar_data.summary"),
    ),
    "googledrive": ("GOOGLEDRIVE_GET_ABOUT", {}, ("user.emailAddress", "response_data.user.emailAddress")),
    "googledocs": ("GOOGLEDOCS_GET_ABOUT", {}, ("user.emailAddress", "response_data.user.emailAddress")),
    "github": (
        "GITHUB_GET_THE_AUTHENTICATED_USER",
        {},
        ("details.login", "details.email", "login", "email", "response_data.login"),
    ),
    "slack": ("SLACK_AUTH_TEST", {}, ("user", "response_data.user", "team", "response_data.team")),
    "notion": ("NOTION_GET_ABOUT_ME", {}, ("response_data.name", "name", "response_data.person.email")),
    "linear": ("LINEAR_GET_CURRENT_USER", {}, ("viewer.email", "viewer.name", "response_data.viewer.email")),
}


def _mask_key(key: str | None) -> str | None:
    """`ak_3kf9dXw2…7Qp1` — enough to recognise the key, never enough to use it."""
    if not key:
        return None
    if len(key) <= 12:
        return key[:2] + "…"
    return f"{key[:8]}…{key[-4:]}"


def _dig(data: Any, path: str) -> Any:
    """Resolve a dot-path inside nested dicts; None when any hop is missing."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def app_owner(slug: str) -> str:
    """Tool-registry owner string per app, so unregistering one app is clean."""
    return f"plugin-connectors:app:{slug}"


def wrapped_tool_name(provider: str, app: str, action_slug: str) -> str:
    action = action_slug.upper().removeprefix(f"{app.upper()}_").lower()
    return f"{provider}__{app}__{action}"


def _tool_policy(t: ProviderToolDef) -> tuple[str, str]:
    """Map provider hints to (policy, risk_level) — same scheme as plugin-mcp."""
    if t.destructive:
        return "prompt_always", "high"
    if t.read_only:
        return "auto_approve", "low"
    return "auto_approve", "medium"


class ConnectorsPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-connectors",
        version="0.1.1",
        description=(
            "Connector aggregator — connect hundreds of platforms (via Composio) "
            "with managed OAuth, agent tools, and event triggers."
        ),
        category="connectors",
        depends_on=["plugin-vault"],
        routes_module="routes",
        settings_tabs=[
            SettingsTab(
                id="connectors",
                label="Connectors",
                icon="plug",
                sort_order=60,
                iframe_src="/api/p/plugin-connectors/ui/settings/",
            ),
        ],
        interfaces={"webui": "interface/webui"},
    )

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._providers: dict[str, ConnectorProvider] = {}
        self._state: dict[str, Any] = {"apps": {}}
        self._seen_event_ids: set[str] = set()
        # 007.001: where the composio key resolved from — "vault" | "env" | "none"
        self._composio_source: str = "none"
        self._composio_key_preview: str | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def on_load(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        # 008.5/phase13: register the live instance so routes find it without
        # walking the core plugin registry.
        from .state import set_plugin
        set_plugin(self)
        if ctx.vault is None:
            log.warning("connectors: vault unavailable; plugin inactive")
            return

        await self._load_state()
        await self._init_composio()
        self._register_management_tools(ctx)

        # 007.001: rebuild the provider live when a composio key is stored,
        # rotated, or deleted (e.g. via the in-chat secure form). Mirrors the
        # LLM router's key refresh — no restart needed.
        async def _on_credential_changed(payload: dict) -> None:
            name = str((payload or {}).get("name", ""))
            if name in (VAULT_COMPOSIO_CANONICAL, VAULT_COMPOSIO_KEY):
                await self._init_composio()

        for _evt in ("credential.stored", "credential.deleted", "credential.rotated"):
            ctx.events.subscribe(_evt, _on_credential_changed)

        # 006.713: publish provider triggers through the neutral core registry.
        from .triggers import ConnectorTriggerSource

        ctx.trigger_sources.register(self.manifest.name, ConnectorTriggerSource(self))

        # Tool restore for connected apps is scheduled from routes.py via a
        # FastAPI startup hook — `luna serve` boots plugins in a throwaway
        # event loop, so a task created here would die with that loop.

    async def on_unload(self) -> None:
        if self._ctx is not None:
            self._ctx.trigger_sources.unregister_plugin(self.manifest.name)
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception:
                pass
        self._providers.clear()
        from .state import set_plugin
        set_plugin(None)

    async def _init_composio(self) -> None:
        """(Re)build the Composio provider through the vault → env chain.

        007.001: canonical vault name → legacy vault name → env
        (`LUNA_COMPOSIO_API_KEY`). `LUNA_COMPOSIO_BASE_URL` is orthogonal —
        when set, ALL composio traffic (including BYOK vault keys) flows
        through that gateway in `gateway` auth mode.
        """
        assert self._ctx is not None

        key: str | None = None
        source = "none"
        for vault_name in (VAULT_COMPOSIO_CANONICAL, VAULT_COMPOSIO_KEY):
            try:
                cred = await self._ctx.vault.get_credential(vault_name)
            except KeyError:
                continue
            if (cred.value or "").strip():
                key, source = cred.value.strip(), "vault"
                break

        if key is None:
            env_val = (self._ctx.get_env("LUNA_COMPOSIO_API_KEY") or "").strip()
            if env_val:
                key, source = env_val, "env"

        base_url = (self._ctx.get_env("LUNA_COMPOSIO_BASE_URL") or "").strip() or None

        old = self._providers.pop("composio", None)
        self._composio_source = source
        # Masked preview (never the full key) so the settings UI can show
        # WHICH key is active when it came from the user's vault.
        self._composio_key_preview = _mask_key(key) if source == "vault" else None
        if key is not None:
            from .providers.composio import ComposioProvider

            if base_url:
                self._providers["composio"] = ComposioProvider(
                    key, base_url=base_url, auth_mode="gateway"
                )
            else:
                self._providers["composio"] = ComposioProvider(key)
            log.info(
                "connectors: provider ready (composio) source=%s gateway=%s",
                source, bool(base_url),
            )
        if old is not None:
            try:
                await old.close()
            except Exception:  # noqa: BLE001
                pass

    def credential_slots(self) -> list[CredentialSlot]:
        return [
            CredentialSlot(
                slug="composio",
                credential_name=VAULT_COMPOSIO_CANONICAL,
                aliases=[VAULT_COMPOSIO_KEY],
                env_key_var="LUNA_COMPOSIO_API_KEY",
                env_base_url_var="LUNA_COMPOSIO_BASE_URL",
                owner=self.manifest.name,
            )
        ]

    def composio_status(self) -> dict[str, Any]:
        """Key-source transparency for the status API (007.001). Never values.

        `source: host` = env key + env base_url → the deployment provides
        composio; the owner needs no key (their own key remains optional).
        """
        assert self._ctx is not None
        base_url_overridden = bool((self._ctx.get_env("LUNA_COMPOSIO_BASE_URL") or "").strip())
        source = self._composio_source
        if source == "env" and base_url_overridden:
            source = "host"
        return {
            "name": "composio",
            "configured": "composio" in self._providers,
            "source": source,
            "base_url_overridden": base_url_overridden,
            "host_name": self._ctx.get_env("LUNA_HOST_NAME"),
            "key_preview": self._composio_key_preview,
        }

    async def restore_connected_apps(self) -> None:
        for slug, app in list(self._state["apps"].items()):
            if not app.get("connected"):
                continue
            try:
                await self.register_app_tools(slug)
            except Exception as e:
                log.warning("connectors: restore failed app=%s error=%s", slug, e)

    # ------------------------------------------------------------------
    # state (vault-backed JSON)
    # ------------------------------------------------------------------

    async def _load_state(self) -> None:
        assert self._ctx is not None
        try:
            cred = await self._ctx.vault.get_credential(VAULT_STATE_KEY)
            self._state = json.loads(cred.value)
        except (KeyError, ValueError):
            self._state = {"apps": {}}
        self._state.setdefault("apps", {})
        self._state.setdefault("trigger_instances", {})
        if self._migrate_state():
            await self._save_state()

    def _migrate_state(self) -> bool:
        """006.713: enabled_playbooks → enabled_triggers (one-time rename)."""
        changed = False
        for app in self._state["apps"].values():
            if "enabled_playbooks" in app:
                app["enabled_triggers"] = bool(app.pop("enabled_playbooks"))
                changed = True
        return changed

    async def _save_state(self) -> None:
        assert self._ctx is not None
        await self._ctx.vault.store_credential(
            VAULT_STATE_KEY, json.dumps(self._state), kind="metadata"
        )

    async def save_state(self) -> None:
        """Public alias for collaborators (ConnectorTriggerSource)."""
        await self._save_state()

    # ------------------------------------------------------------------
    # public API (used by routes + agent tools)
    # ------------------------------------------------------------------

    def provider_for(self, slug: str) -> ConnectorProvider | None:
        app = self._state["apps"].get(slug)
        provider_name = (app or {}).get("provider") or "composio"
        return self._providers.get(provider_name)

    def default_provider(self) -> ConnectorProvider | None:
        return self._providers.get("composio")

    @property
    def configured(self) -> bool:
        return bool(self._providers)

    def app_states(self) -> list[dict[str, Any]]:
        out = []
        for slug, app in self._state["apps"].items():
            out.append({"slug": slug, **app})
        out.sort(key=lambda a: (not a.get("connected", False), a["slug"]))
        return out

    async def prompt_sections(self) -> list[str]:
        """007.010: tell the agent which apps are ALREADY connected so it stops
        re-discovering them (reaching for MCP, re-gathering credentials). Reads
        only in-memory state — no DB, no live identity probe. Lists connected
        apps only (never the full catalog); returns [] when nothing is
        connected, so no empty header is emitted."""
        if not self.configured:
            return []

        connected = [a for a in self.app_states() if a.get("connected")]
        if not connected:
            return []

        lines = [
            "## Connected apps (managed integrations)",
            "These platforms are ALREADY connected via managed OAuth. To act on "
            "one, load its skill (below) — do NOT add an MCP server or re-gather "
            "credentials for an app listed here. Prefer these connectors over "
            "creating new MCP servers when both could serve the request.",
            "Use `connector_list_connected` for full status, "
            "`connector_search_apps` to find a platform that is not listed here.",
            "",
        ]
        for app in connected:
            slug = app["slug"]
            name = app.get("name", slug)
            identity = app.get("account_identity")
            who = f"{name}, {identity}" if identity else name
            if app.get("enabled_agent"):
                lines.append(
                    f"- `{slug}` ({who}): ready — call "
                    f"load_skill('connector-{slug}') to use its tools."
                )
            else:
                lines.append(
                    f"- `{slug}` ({who}): connected but NOT enabled for agent — "
                    "ask the owner to enable agent access in Settings → "
                    f"Connectors, or call connector_request_enable(app='{slug}')."
                )

        return ["\n".join(lines)]

    async def upsert_app(self, info: AppInfo, provider: str = "composio") -> dict[str, Any]:
        app = self._state["apps"].setdefault(
            info.slug,
            {
                "provider": provider,
                "enabled_agent": False,
                "enabled_triggers": False,
                "connected": False,
                "auth_config_id": None,
                "connected_account_id": None,
            },
        )
        app.update(
            {
                "name": info.name,
                "logo": info.logo,
                "no_auth": info.no_auth,
                "description": info.description,
                "tools_count": info.tools_count,
                "triggers_count": info.triggers_count,
            }
        )
        await self._save_state()
        return app

    async def _emit_app_changed(self, slug: str) -> None:
        """Notify the UI (via the /api/events SSE bridge) that an app's
        connection/exposure state changed — keeps open settings tabs live."""
        if self._ctx is None:
            return
        app = self._state["apps"].get(slug, {})
        await self._ctx.events.emit(
            "connectors.app_changed",
            {
                "slug": slug,
                "connected": app.get("connected", False),
                "enabled_agent": app.get("enabled_agent", False),
                "enabled_triggers": app.get("enabled_triggers", False),
            },
        )

    async def connect_app(self, slug: str) -> dict[str, Any]:
        """Start connecting an app. No-auth apps connect instantly; OAuth apps
        return a redirect_url the user must visit."""
        provider = self.default_provider()
        if provider is None:
            raise ConnectorProviderError("No connector provider configured")

        # Always refresh catalog info before connecting — auth requirements
        # may differ between the list and detail endpoints (or have changed).
        info = await provider.get_app(slug)
        if info is None:
            raise ConnectorProviderError(f"Unknown app: {slug}")
        app = await self.upsert_app(info)

        if app.get("no_auth"):
            app["connected"] = True
            await self._save_state()
            await self.register_app_tools(slug)
            await self._emit_app_changed(slug)
            return {"connected": True}

        # 007.014: API-key / custom-auth toolkits have no managed OAuth default.
        # Don't call the provider yet — return the field spec so the UI collects
        # the credential(s), then completes via connect_app_with_key().
        if info.needs_custom_auth:
            return {
                "connected": False,
                "needs_key": True,
                "auth_mode": info.auth_mode,
                "fields": [f.to_dict() for f in info.auth_fields],
            }

        init = await provider.initiate_connection(slug)
        app["auth_config_id"] = init.auth_config_id
        app["connected_account_id"] = init.connected_account_id
        await self._save_state()
        return {"connected": False, "redirect_url": init.redirect_url}

    async def connect_app_with_key(
        self, slug: str, credentials: dict[str, str]
    ) -> dict[str, Any]:
        """Complete a custom-auth connection with user-supplied credentials.
        API-key toolkits activate immediately; anything else falls back to the
        existing OAuth-style polling via refresh_app()."""
        provider = self.default_provider()
        if provider is None:
            raise ConnectorProviderError("No connector provider configured")
        info = await provider.get_app(slug)
        if info is None:
            raise ConnectorProviderError(f"Unknown app: {slug}")
        app = await self.upsert_app(info)

        init = await provider.initiate_custom_connection(slug, credentials)
        app["auth_config_id"] = init.auth_config_id
        app["connected_account_id"] = init.connected_account_id
        if (init.status or "").upper() == "ACTIVE":
            app["connected"] = True
            await self._save_state()
            await self.register_app_tools(slug)
            await self._emit_app_changed(slug)
            return {"connected": True}
        await self._save_state()
        return {"connected": False, "status": init.status}

    async def refresh_app(self, slug: str) -> dict[str, Any]:
        """Poll the provider for connection completion (after OAuth)."""
        app = self._state["apps"].get(slug)
        provider = self.provider_for(slug)
        if app is None or provider is None:
            raise ConnectorProviderError(f"Unknown app: {slug}")
        if app.get("connected"):
            return {"connected": True}
        account_id = app.get("connected_account_id")
        if not account_id:
            return {"connected": False}
        status = await provider.get_account_status(account_id)
        if status == "ACTIVE":
            app["connected"] = True
            await self._save_state()
            await self.register_app_tools(slug)
            await self._emit_app_changed(slug)
        return {"connected": app.get("connected", False), "status": status}

    async def disconnect_app(self, slug: str) -> None:
        app = self._state["apps"].get(slug)
        if app is None:
            return
        await self._release_app_trigger_instances(slug)
        provider = self.provider_for(slug)
        account_id = app.get("connected_account_id")
        if provider is not None and account_id:
            await provider.delete_account(account_id)
        app.update(
            {
                "connected": False,
                "connected_account_id": None,
                "auth_config_id": None,
                "enabled_agent": False,
                "enabled_triggers": False,
                "account_identity": None,
            }
        )
        await self._save_state()
        self.unregister_app_tools(slug)
        await self._emit_app_changed(slug)

    async def account_details(self, slug: str) -> dict[str, Any]:
        """Identifying details of the connected account for the settings UI.

        Combines sanitized provider metadata (status, auth scheme, connect
        date) with a one-time identity probe (email / username) that is cached
        in plugin state. Never returns keys or tokens.
        """
        app = self._state["apps"].get(slug)
        if app is None:
            raise ConnectorProviderError(f"Unknown app: {slug}")
        out: dict[str, Any] = {
            "slug": slug,
            "connected": bool(app.get("connected")),
            "no_auth": bool(app.get("no_auth")),
            "identity": app.get("account_identity"),
            "status": None,
            "auth_scheme": None,
            "connected_at": None,
        }
        if not out["connected"]:
            return out
        provider = self.provider_for(slug)
        account_id = app.get("connected_account_id")
        if provider is None or not account_id:
            # no-auth apps have no per-user account at the provider
            return out
        try:
            info = await provider.get_account_info(account_id)
            out["status"] = info.get("status")
            out["auth_scheme"] = info.get("auth_scheme")
            out["connected_at"] = info.get("created_at")
        except ConnectorProviderError as e:
            log.warning("connectors: account info failed app=%s error=%s", slug, e)
        if out["identity"] is None and out["status"] in (None, "ACTIVE"):
            out["identity"] = await self._probe_identity(slug, app, provider, account_id)
        return out

    async def _probe_identity(
        self, slug: str, app: dict[str, Any], provider: ConnectorProvider, account_id: str
    ) -> str | None:
        probe = _IDENTITY_PROBES.get(slug)
        execute = getattr(provider, "execute", None)
        if probe is None or execute is None:
            return None
        tool_slug, args, paths = probe
        try:
            data = await execute(tool_slug, dict(args), connected_account_id=account_id)
        except ConnectorProviderError as e:
            log.warning("connectors: identity probe failed app=%s error=%s", slug, e)
            return None
        for path in paths:
            val = _dig(data, path)
            if isinstance(val, str) and val.strip():
                label = val.strip()[:120]
                app["account_identity"] = label
                await self._save_state()
                return label
        return None

    async def set_exposure(self, slug: str, agent: bool | None, triggers: bool | None) -> dict[str, Any]:
        app = self._state["apps"].get(slug)
        if app is None:
            raise ConnectorProviderError(f"Unknown app: {slug}")
        if agent is not None:
            app["enabled_agent"] = bool(agent)
        if triggers is not None:
            app["enabled_triggers"] = bool(triggers)
            if not triggers:
                # Toggle off ⇒ tear down any live provider instances for the app.
                await self._release_app_trigger_instances(slug)
        await self._save_state()
        if app.get("connected"):
            await self.register_app_tools(slug)
        await self._emit_app_changed(slug)
        return app

    # ------------------------------------------------------------------
    # trigger source collaborators (see triggers.py)
    # ------------------------------------------------------------------

    def apps_with_triggers_enabled(self) -> list[tuple[str, dict[str, Any]]]:
        return [
            (slug, app)
            for slug, app in self._state["apps"].items()
            if app.get("connected") and app.get("enabled_triggers")
        ]

    def trigger_instances(self) -> dict[str, Any]:
        return self._state.setdefault("trigger_instances", {})

    def app_for_trigger(self, trigger_slug: str) -> tuple[str, dict[str, Any] | None]:
        """Match a provider trigger slug (e.g. gmail_new_gmail_message) to its app."""
        candidates = [
            (slug, app)
            for slug, app in self._state["apps"].items()
            if trigger_slug.startswith(f"{slug}_") or trigger_slug == slug
        ]
        if not candidates:
            return "", None
        # Longest slug wins (e.g. "googledrive" over "google").
        slug, app = max(candidates, key=lambda c: len(c[0]))
        return slug, app

    async def _release_app_trigger_instances(self, slug: str) -> None:
        instances = self.trigger_instances()
        doomed = [k for k, v in instances.items() if v.get("app") == slug]
        provider = self.provider_for(slug)
        for key in doomed:
            entry = instances.pop(key)
            if provider is not None:
                try:
                    await provider.delete_trigger_instance(str(entry["id"]))
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "connectors: trigger release failed trigger=%s error=%s", key, e
                    )
        if doomed:
            await self._save_state()
            log.info("connectors: triggers released app=%s count=%s", slug, len(doomed))

    # ------------------------------------------------------------------
    # dynamic tool + skill registration
    # ------------------------------------------------------------------

    async def register_app_tools(self, slug: str) -> int:
        """(Re-)register an app's tools and skill from provider schemas."""
        assert self._ctx is not None
        app = self._state["apps"].get(slug)
        provider = self.provider_for(slug)
        if app is None or provider is None or not app.get("connected"):
            return 0

        tools = await provider.list_tools(slug, limit=MAX_TOOLS_PER_APP)
        owner = app_owner(slug)
        registry = self._ctx.tool_registry
        registry.unregister_plugin(owner)

        account_id = app.get("connected_account_id")
        provider_name = app.get("provider", "composio")
        wrapped_names: list[str] = []

        for t in tools:
            name = wrapped_tool_name(provider_name, slug, t.slug)
            policy, risk = _tool_policy(t)

            async def _handler(
                _tool_slug: str = t.slug,
                _provider: ConnectorProvider = provider,
                _account: str | None = account_id,
                **kwargs: Any,
            ) -> Any:
                return await _provider.execute(_tool_slug, kwargs, _account)

            registry.register(
                owner,
                ToolDef(
                    name=name,
                    description=t.description or t.name,
                    parameters=t.parameters,
                    policy=policy,
                    risk_level=risk,
                    timeout_seconds=60,
                ),
                _handler,
                skill_gated=True,
            )
            wrapped_names.append(name)

        self._sync_skill(slug, app, wrapped_names)
        log.info("connectors: tools registered app=%s count=%s", slug, len(wrapped_names))
        return len(wrapped_names)

    def unregister_app_tools(self, slug: str) -> None:
        assert self._ctx is not None
        self._ctx.tool_registry.unregister_plugin(app_owner(slug))
        if self._ctx.skill_registry is not None:
            self._ctx.skill_registry.unregister_plugin(app_owner(slug))

    def _sync_skill(self, slug: str, app: dict[str, Any], tool_names: list[str]) -> None:
        """Register the app's skill only when agent exposure is on."""
        assert self._ctx is not None
        if self._ctx.skill_registry is None:
            return
        owner = app_owner(slug)
        self._ctx.skill_registry.unregister_plugin(owner)
        if not app.get("enabled_agent") or not tool_names:
            return
        name = app.get("name", slug)
        self._ctx.skill_registry.register(
            owner,
            SkillDef(
                name=f"connector-{slug}",
                description=f"{name} — {app.get('description', '')[:140]}",
                body=(
                    f"You now have access to {name} via the connectors plugin. "
                    f"Tools are named {app.get('provider', 'composio')}__{slug}__<action>. "
                    "Call them with arguments matching each tool's schema. "
                    "Results come from the live service — report errors honestly."
                ),
                tools=tool_names,
            ),
        )

    # ------------------------------------------------------------------
    # events ingress (used by routes)
    # ------------------------------------------------------------------

    async def handle_provider_event(self, provider: str, payload: dict[str, Any]) -> str | None:
        """Normalize a provider webhook into `connector.{app}.{slug}` on the bus."""
        assert self._ctx is not None
        event_id = str(payload.get("id") or payload.get("event_id") or "")
        if event_id:
            if event_id in self._seen_event_ids:
                return None
            self._seen_event_ids.add(event_id)
            if len(self._seen_event_ids) > 5000:
                self._seen_event_ids.clear()

        from .triggers import normalized_event

        trigger_slug = str(
            payload.get("trigger_slug")
            or (payload.get("metadata") or {}).get("trigger_slug")
            or payload.get("type")
            or "event"
        ).lower()
        app_slug = str(
            payload.get("toolkit_slug")
            or (payload.get("metadata") or {}).get("toolkit_slug")
            or trigger_slug.split("_")[0]
        ).lower()
        event_name = normalized_event(app_slug, trigger_slug)
        trigger_slug = event_name.rsplit(".", 1)[1]
        await self._ctx.events.emit(
            event_name,
            {"provider": provider, "app": app_slug, "trigger": trigger_slug, "data": payload},
        )
        log.info("connectors: event bus_event=%s provider=%s", event_name, provider)
        return event_name

    # ------------------------------------------------------------------
    # agent management tools (always available, never skill-gated)
    # ------------------------------------------------------------------

    def _register_management_tools(self, ctx: PluginContext) -> None:
        plugin = self.manifest.name

        async def _search_apps(*, query: str) -> Any:
            provider = self.default_provider()
            if provider is None:
                return {
                    "error": "No connector provider configured. "
                    "Ask the owner to add a Composio API key in Settings → Connectors."
                }
            apps = await provider.list_apps(search=query, limit=10)
            state = self._state["apps"]
            return [
                {
                    "slug": a.slug,
                    "name": a.name,
                    "description": a.description[:140],
                    "tools_count": a.tools_count,
                    "requires_oauth": not a.no_auth,
                    "connected": bool(state.get(a.slug, {}).get("connected")),
                    "enabled_for_agent": bool(state.get(a.slug, {}).get("enabled_agent")),
                }
                for a in apps
            ]

        async def _list_connected() -> Any:
            return [
                {
                    "slug": a["slug"],
                    "name": a.get("name", a["slug"]),
                    "connected": a.get("connected", False),
                    "enabled_for_agent": a.get("enabled_agent", False),
                    "enabled_for_triggers": a.get("enabled_triggers", False),
                    "skill": f"connector-{a['slug']}" if a.get("enabled_agent") else None,
                }
                for a in self.app_states()
            ]

        async def _request_enable(*, app: str, exposure: str = "agent") -> Any:
            provider = self.default_provider()
            if provider is None:
                return {"error": "No connector provider configured."}
            slug = app.lower().strip()
            try:
                result = await self.connect_app(slug)
            except ConnectorProviderError:
                return {"error": f"No connector found for '{app}'."}
            await self.set_exposure(
                slug,
                agent=exposure in ("agent", "both"),
                triggers=exposure in ("triggers", "both"),
            )
            if result.get("connected"):
                return {
                    "enabled": True,
                    "app": slug,
                    "skill": f"connector-{slug}",
                    "note": f"Connected. Use load_skill('connector-{slug}') to access its tools.",
                }
            return {
                "enabled": True,
                "connected": False,
                "app": slug,
                "auth_url": result.get("redirect_url"),
                "note": (
                    "The app is enabled but needs the owner to authorize it. "
                    "Give the owner this auth_url to complete the connection, then "
                    "they can confirm in Settings → Connectors."
                ),
            }

        ctx.tool_registry.register(
            plugin,
            ToolDef(
                name="connector_search_apps",
                description=(
                    "Search the connector catalog (hundreds of platforms: CRMs, email, "
                    "project management, dev tools...) to see if an integration exists "
                    "for a platform. Returns connection/enablement status per app."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Platform name, e.g. 'notion'"}
                    },
                    "required": ["query"],
                },
                policy="auto_approve",
                risk_level="low",
            ),
            _search_apps,
        )

        ctx.tool_registry.register(
            plugin,
            ToolDef(
                name="connector_list_connected",
                description=(
                    "List connector apps and their status (connected, enabled for agent, "
                    "enabled for triggers, skill name to load)."
                ),
                parameters={"type": "object", "properties": {}},
                policy="auto_approve",
                risk_level="low",
            ),
            _list_connected,
        )

        ctx.tool_registry.register(
            plugin,
            ToolDef(
                name="connector_request_enable",
                description=(
                    "Enable a connector app for use (requires owner approval). For apps "
                    "needing OAuth this returns an auth_url the owner must visit. "
                    "exposure: 'agent' (chat tools), 'triggers' (event triggers for "
                    "playbooks and other listeners), or 'both'."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "app": {"type": "string", "description": "App slug, e.g. 'hackernews'"},
                        "exposure": {
                            "type": "string",
                            "enum": ["agent", "triggers", "both"],
                            "description": "What to expose (default: agent)",
                        },
                    },
                    "required": ["app"],
                },
                policy="prompt_always",
                risk_level="medium",
                # Connect + schema fetch + registration is several Composio
                # round-trips; the default 30s cuts registration short.
                timeout_seconds=180,
            ),
            _request_enable,
        )
