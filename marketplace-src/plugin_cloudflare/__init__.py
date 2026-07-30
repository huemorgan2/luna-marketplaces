"""plugin-cloudflare — Cloudflare infrastructure management tools.

Provides DNS, cache, Workers, KV, and Pages management via the Cloudflare API.
Requires an API token stored in vault. Ships OFF by default; activate by pasting
your Cloudflare API token in Settings.
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

from .client import CloudflareClient

log = logging.getLogger("plugin-cloudflare")

VAULT_KEY_TOKEN = "plugin_cloudflare.api_token"
VAULT_KEY_ACCOUNT = "plugin_cloudflare.account_id"

# --- Tool definitions ---

_CF_LIST_ZONES = ToolDef(
    name="cf_list_zones",
    description="List all Cloudflare zones (domains) on the account.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Filter by domain name (optional)."},
            "page": {"type": "integer", "description": "Page number (default 1)."},
            "per_page": {"type": "integer", "description": "Results per page (default 20, max 50)."},
        },
    },
    skill_gated=True,
)

_CF_LIST_DNS_RECORDS = ToolDef(
    name="cf_list_dns_records",
    description="List DNS records for a zone.",
    parameters={
        "type": "object",
        "properties": {
            "zone_id": {"type": "string", "description": "The zone ID."},
            "type": {"type": "string", "description": "Filter by record type (A, AAAA, CNAME, etc.)."},
            "name": {"type": "string", "description": "Filter by record name."},
        },
        "required": ["zone_id"],
    },
    skill_gated=True,
)

_CF_CREATE_DNS_RECORD = ToolDef(
    name="cf_create_dns_record",
    description="Create a new DNS record in a zone.",
    parameters={
        "type": "object",
        "properties": {
            "zone_id": {"type": "string", "description": "The zone ID."},
            "type": {"type": "string", "description": "Record type (A, AAAA, CNAME, MX, TXT, etc.)."},
            "name": {"type": "string", "description": "DNS record name (e.g. 'example.com' or 'sub.example.com')."},
            "content": {"type": "string", "description": "Record content (IP for A, target for CNAME, etc.)."},
            "ttl": {"type": "integer", "description": "TTL in seconds (1 = auto)."},
            "proxied": {"type": "boolean", "description": "Whether to proxy through Cloudflare (default false)."},
        },
        "required": ["zone_id", "type", "name", "content"],
    },
    skill_gated=True,
)

_CF_UPDATE_DNS_RECORD = ToolDef(
    name="cf_update_dns_record",
    description="Update an existing DNS record.",
    parameters={
        "type": "object",
        "properties": {
            "zone_id": {"type": "string", "description": "The zone ID."},
            "record_id": {"type": "string", "description": "The DNS record ID."},
            "type": {"type": "string", "description": "Record type."},
            "name": {"type": "string", "description": "Record name."},
            "content": {"type": "string", "description": "Record content."},
            "ttl": {"type": "integer", "description": "TTL in seconds (1 = auto)."},
            "proxied": {"type": "boolean", "description": "Whether to proxy through Cloudflare."},
        },
        "required": ["zone_id", "record_id"],
    },
    skill_gated=True,
)

_CF_DELETE_DNS_RECORD = ToolDef(
    name="cf_delete_dns_record",
    description="Delete a DNS record from a zone.",
    parameters={
        "type": "object",
        "properties": {
            "zone_id": {"type": "string", "description": "The zone ID."},
            "record_id": {"type": "string", "description": "The DNS record ID to delete."},
        },
        "required": ["zone_id", "record_id"],
    },
    skill_gated=True,
)

_CF_PURGE_CACHE = ToolDef(
    name="cf_purge_cache",
    description="Purge the Cloudflare cache for a zone. Can purge everything or specific files.",
    parameters={
        "type": "object",
        "properties": {
            "zone_id": {"type": "string", "description": "The zone ID."},
            "purge_everything": {"type": "boolean", "description": "Purge all cached files (default false)."},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of URLs to purge from cache.",
            },
        },
        "required": ["zone_id"],
    },
    skill_gated=True,
)

_CF_LIST_WORKERS = ToolDef(
    name="cf_list_workers",
    description="List all Workers scripts on the account.",
    parameters={"type": "object", "properties": {}},
    skill_gated=True,
)

_CF_GET_WORKER = ToolDef(
    name="cf_get_worker",
    description="Get a Worker script's source code.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The worker script name."},
        },
        "required": ["name"],
    },
    skill_gated=True,
)

_CF_DEPLOY_WORKER = ToolDef(
    name="cf_deploy_worker",
    description="Deploy (create or update) a Worker script.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The worker script name."},
            "script": {"type": "string", "description": "The JavaScript source code of the worker."},
        },
        "required": ["name", "script"],
    },
    skill_gated=True,
)

_CF_LIST_KV_NAMESPACES = ToolDef(
    name="cf_list_kv_namespaces",
    description="List all KV namespaces on the account.",
    parameters={"type": "object", "properties": {}},
    skill_gated=True,
)

_CF_KV_GET = ToolDef(
    name="cf_kv_get",
    description="Get a value from a KV namespace.",
    parameters={
        "type": "object",
        "properties": {
            "namespace_id": {"type": "string", "description": "The KV namespace ID."},
            "key": {"type": "string", "description": "The key to retrieve."},
        },
        "required": ["namespace_id", "key"],
    },
    skill_gated=True,
)

_CF_KV_PUT = ToolDef(
    name="cf_kv_put",
    description="Write a value to a KV namespace.",
    parameters={
        "type": "object",
        "properties": {
            "namespace_id": {"type": "string", "description": "The KV namespace ID."},
            "key": {"type": "string", "description": "The key to write."},
            "value": {"type": "string", "description": "The value to store."},
        },
        "required": ["namespace_id", "key", "value"],
    },
    skill_gated=True,
)

_CF_KV_DELETE = ToolDef(
    name="cf_kv_delete",
    description="Delete a key from a KV namespace.",
    parameters={
        "type": "object",
        "properties": {
            "namespace_id": {"type": "string", "description": "The KV namespace ID."},
            "key": {"type": "string", "description": "The key to delete."},
        },
        "required": ["namespace_id", "key"],
    },
    skill_gated=True,
)

_CF_LIST_PAGES_PROJECTS = ToolDef(
    name="cf_list_pages_projects",
    description="List all Cloudflare Pages projects on the account.",
    parameters={"type": "object", "properties": {}},
    skill_gated=True,
)

_CF_GET_PAGES_PROJECT = ToolDef(
    name="cf_get_pages_project",
    description="Get details of a Cloudflare Pages project.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The Pages project name."},
        },
        "required": ["name"],
    },
    skill_gated=True,
)

ALL_TOOLS = [
    _CF_LIST_ZONES, _CF_LIST_DNS_RECORDS, _CF_CREATE_DNS_RECORD,
    _CF_UPDATE_DNS_RECORD, _CF_DELETE_DNS_RECORD,
    _CF_PURGE_CACHE,
    _CF_LIST_WORKERS, _CF_GET_WORKER, _CF_DEPLOY_WORKER,
    _CF_LIST_KV_NAMESPACES, _CF_KV_GET, _CF_KV_PUT, _CF_KV_DELETE,
    _CF_LIST_PAGES_PROJECTS, _CF_GET_PAGES_PROJECT,
]

# --- Skills ---

SKILLS = [
    SkillDef(
        name="cloudflare-dns",
        description="Manage Cloudflare DNS zones and records — list, create, update, delete.",
        body="Use cf_list_zones to find zone IDs, then cf_list_dns_records / cf_create_dns_record / cf_update_dns_record / cf_delete_dns_record.",
        tools=["cf_list_zones", "cf_list_dns_records", "cf_create_dns_record", "cf_update_dns_record", "cf_delete_dns_record"],
    ),
    SkillDef(
        name="cloudflare-cache",
        description="Purge Cloudflare CDN cache — everything or specific URLs.",
        body="Use cf_purge_cache with purge_everything=true or a files list.",
        tools=["cf_purge_cache"],
    ),
    SkillDef(
        name="cloudflare-workers",
        description="Manage Cloudflare Workers — list, read, and deploy scripts.",
        body="Use cf_list_workers, cf_get_worker, cf_deploy_worker.",
        tools=["cf_list_workers", "cf_get_worker", "cf_deploy_worker"],
    ),
    SkillDef(
        name="cloudflare-kv",
        description="Manage Cloudflare Workers KV — list namespaces, get/put/delete keys.",
        body="Use cf_list_kv_namespaces, cf_kv_get, cf_kv_put, cf_kv_delete.",
        tools=["cf_list_kv_namespaces", "cf_kv_get", "cf_kv_put", "cf_kv_delete"],
    ),
    SkillDef(
        name="cloudflare-pages",
        description="View Cloudflare Pages projects and their deployment details.",
        body="Use cf_list_pages_projects, cf_get_pages_project.",
        tools=["cf_list_pages_projects", "cf_get_pages_project"],
    ),
]


class CloudflarePlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-cloudflare",
        version="0.1.1",
        description="Cloudflare infrastructure management — DNS, cache, Workers, KV, Pages.",
        category="connectors",
        depends_on=["plugin-vault"],
        auto_load=False,
        routes_module="routes",
        settings_tabs=[
            SettingsTab(
                id="cloudflare",
                label="Cloudflare",
                icon="cloud",
                sort_order=60,
                iframe_src="/api/p/plugin-cloudflare/ui/settings/",
            ),
        ],
    )

    def __init__(self) -> None:
        self._client: CloudflareClient | None = None
        self._active: bool = False

    @property
    def active(self) -> bool:
        return self._active

    async def on_load(self, ctx: PluginContext) -> None:
        # Routes mount via routes.register_routes (loader pushes them ahead of
        # the SPA catch-all); no self-mount here.
        vault = ctx.vault
        if vault is None:
            log.warning("Vault not available; plugin-cloudflare inactive")
            self._active = False
            return

        try:
            token_cred = await vault.get_credential(VAULT_KEY_TOKEN)
            api_token = token_cred.value
        except KeyError:
            api_token = None

        try:
            acct_cred = await vault.get_credential(VAULT_KEY_ACCOUNT)
            account_id = acct_cred.value
        except KeyError:
            account_id = None

        if not api_token or not account_id:
            log.info("plugin-cloudflare: API token or account ID not in vault; tools inactive")
            self._active = False
            return

        self._client = CloudflareClient(api_token=api_token, account_id=account_id)
        self._active = True

        self._register_tools(ctx)
        self._register_skills(ctx)

        log.info("plugin-cloudflare loaded (tools=%d skills=%d)", len(ALL_TOOLS), len(SKILLS))

    def _register_tools(self, ctx: PluginContext) -> None:
        client = self._client
        assert client is not None

        async def _list_zones(
            name: str | None = None, page: int = 1, per_page: int = 20
        ) -> dict[str, Any]:
            params: dict[str, Any] = {"page": page, "per_page": per_page}
            if name:
                params["name"] = name
            return await client.list_zones(params)

        async def _list_dns_records(
            zone_id: str, type: str | None = None, name: str | None = None
        ) -> dict[str, Any]:
            params: dict[str, Any] = {}
            if type:
                params["type"] = type
            if name:
                params["name"] = name
            return await client.list_dns_records(zone_id, params or None)

        async def _create_dns_record(
            zone_id: str,
            type: str,
            name: str,
            content: str,
            ttl: int = 1,
            proxied: bool = False,
        ) -> dict[str, Any]:
            data: dict[str, Any] = {
                "type": type, "name": name, "content": content, "ttl": ttl, "proxied": proxied,
            }
            return await client.create_dns_record(zone_id, data)

        async def _update_dns_record(
            zone_id: str,
            record_id: str,
            type: str | None = None,
            name: str | None = None,
            content: str | None = None,
            ttl: int | None = None,
            proxied: bool | None = None,
        ) -> dict[str, Any]:
            data: dict[str, Any] = {}
            if type is not None:
                data["type"] = type
            if name is not None:
                data["name"] = name
            if content is not None:
                data["content"] = content
            if ttl is not None:
                data["ttl"] = ttl
            if proxied is not None:
                data["proxied"] = proxied
            return await client.update_dns_record(zone_id, record_id, data)

        async def _delete_dns_record(zone_id: str, record_id: str) -> dict[str, Any]:
            return await client.delete_dns_record(zone_id, record_id)

        async def _purge_cache(
            zone_id: str,
            purge_everything: bool = False,
            files: list[str] | None = None,
        ) -> dict[str, Any]:
            data: dict[str, Any] = {}
            if purge_everything:
                data["purge_everything"] = True
            elif files:
                data["files"] = files
            else:
                data["purge_everything"] = True
            return await client.purge_cache(zone_id, data)

        async def _list_workers() -> dict[str, Any]:
            return await client.list_workers()

        async def _get_worker(name: str) -> dict[str, Any]:
            return await client.get_worker(name)

        async def _deploy_worker(name: str, script: str) -> dict[str, Any]:
            return await client.deploy_worker(name, script)

        async def _list_kv_namespaces() -> dict[str, Any]:
            return await client.list_kv_namespaces()

        async def _kv_get(namespace_id: str, key: str) -> dict[str, Any]:
            return await client.kv_get(namespace_id, key)

        async def _kv_put(namespace_id: str, key: str, value: str) -> dict[str, Any]:
            return await client.kv_put(namespace_id, key, value)

        async def _kv_delete(namespace_id: str, key: str) -> dict[str, Any]:
            return await client.kv_delete(namespace_id, key)

        async def _list_pages_projects() -> dict[str, Any]:
            return await client.list_pages_projects()

        async def _get_pages_project(name: str) -> dict[str, Any]:
            return await client.get_pages_project(name)

        handlers = [
            (_CF_LIST_ZONES, _list_zones),
            (_CF_LIST_DNS_RECORDS, _list_dns_records),
            (_CF_CREATE_DNS_RECORD, _create_dns_record),
            (_CF_UPDATE_DNS_RECORD, _update_dns_record),
            (_CF_DELETE_DNS_RECORD, _delete_dns_record),
            (_CF_PURGE_CACHE, _purge_cache),
            (_CF_LIST_WORKERS, _list_workers),
            (_CF_GET_WORKER, _get_worker),
            (_CF_DEPLOY_WORKER, _deploy_worker),
            (_CF_LIST_KV_NAMESPACES, _list_kv_namespaces),
            (_CF_KV_GET, _kv_get),
            (_CF_KV_PUT, _kv_put),
            (_CF_KV_DELETE, _kv_delete),
            (_CF_LIST_PAGES_PROJECTS, _list_pages_projects),
            (_CF_GET_PAGES_PROJECT, _get_pages_project),
        ]

        for tool_def, handler in handlers:
            ctx.tool_registry.register(
                self.manifest.name, tool_def, handler, skill_gated=True
            )

    def _register_skills(self, ctx: PluginContext) -> None:
        if ctx.skill_registry is None:
            return
        for skill in SKILLS:
            ctx.skill_registry.register(self.manifest.name, skill)

    async def on_unload(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
        self._active = False
