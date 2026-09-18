"""plugin-web-access — gives the agent eyes on the internet (005.902).

Three auto-approved tools (Luna is trusted to use the internet freely):
  - web_search  — Tavily (default) or Google, configurable via env
  - web_fetch   — GET a URL, return readable text
  - http_request — generic HTTP client for API calls

Provider + API keys come from env for this slice (see search.py / NOTES.md);
a Settings-UI/vault-backed selector is the deferred slice.

Events: web.search / web.fetch / web.request feed cost-tracking + action logging.

Security note: per the 005.902 plan this is OPEN internet access — no domain
allowlist/SSRF guard. That's an accepted, documented product decision for a
local single-user agent; a guard + vault credential injection are listed as
future work in the plan (and echoed in NOTES.md).
"""

from __future__ import annotations

import logging
from typing import Any

from luna_sdk import CredentialSlot, LunaPlugin, PluginContext, PluginManifest, ToolDef

from .fetch import run_fetch
from .http_client import run_request
from .search import run_search

log = logging.getLogger("plugin-web-access")


_WEB_SEARCH_DEF = ToolDef(
    name="web_search",
    description=(
        "Search the live web and return a concise answer plus source results "
        "(title, url, snippet). Use this for current events, recent facts, or "
        "anything you may not know or that may have changed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {
                "type": "integer",
                "description": "How many results to return (1-10, default 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    policy="auto_approve",
)

_WEB_FETCH_DEF = ToolDef(
    name="web_fetch",
    description=(
        "Fetch a web page by URL and return its readable text content (chrome "
        "like nav/scripts stripped). Use to read or summarize a specific page "
        "the user gives you or that a search returned."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The http(s) URL to fetch."}},
        "required": ["url"],
    },
    policy="auto_approve",
    timeout_seconds=40,
)

_HTTP_REQUEST_DEF = ToolDef(
    name="http_request",
    description=(
        "Make an arbitrary HTTP request to an API (GET/POST/PUT/PATCH/DELETE). "
        "Returns status, headers, and body (JSON auto-parsed). Use for calling "
        "REST APIs. Do NOT use to fetch readable pages — use web_fetch for that."
    ),
    parameters={
        "type": "object",
        "properties": {
            "method": {"type": "string", "description": "HTTP method (GET, POST, ...)."},
            "url": {"type": "string", "description": "The http(s) URL."},
            "headers": {"type": "object", "description": "Optional request headers."},
            "body": {
                "type": ["string", "object"],
                "description": "Optional request body (object → JSON, string → raw).",
            },
            "timeout": {"type": "integer", "description": "Timeout seconds (default 30).", "default": 30},
        },
        "required": ["method", "url"],
    },
    policy="auto_approve",
    timeout_seconds=40,
)


class WebAccessPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-web-access",
        shown_name="Web Access",
        icon="search",
        image="assets/icon.png",
        version="0.3.1",
        description="Web search (Tavily/Google), page fetch, and HTTP client — live internet access.",
        tools=[_WEB_SEARCH_DEF, _WEB_FETCH_DEF, _HTTP_REQUEST_DEF],
    )

    def credential_slots(self) -> list[CredentialSlot]:
        return [
            CredentialSlot(
                slug="tavily",
                credential_name="tavily_api_key",
                env_key_var="LUNA_TAVILY_API_KEY",
                env_base_url_var="LUNA_TAVILY_BASE_URL",
                owner=self.manifest.name,
            ),
            CredentialSlot(
                slug="google_search",
                credential_name="google_search_api_key",
                env_key_var="LUNA_GOOGLE_SEARCH_API_KEY",
                env_base_url_var=None,
                owner=self.manifest.name,
            ),
        ]

    async def on_load(self, ctx: PluginContext) -> None:
        events = ctx.events
        vault = ctx.vault

        async def _search(query: str, max_results: int = 5) -> dict[str, Any]:
            out = await run_search(query, max_results, vault=vault)
            await events.emit(
                "web.search",
                {
                    "query": query,
                    "provider": out.get("provider"),
                    "result_count": out.get("result_count", 0),
                    "error": out.get("error"),
                },
            )
            return out

        async def _fetch(url: str) -> dict[str, Any]:
            out = await run_fetch(url)
            await events.emit(
                "web.fetch",
                {"url": url, "status": out.get("status"), "content_length": out.get("content_length"), "error": out.get("error")},
            )
            return out

        async def _http(
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            body: Any = None,
            timeout: int = 30,
        ) -> dict[str, Any]:
            out = await run_request(method, url, headers=headers, body=body, timeout=timeout)
            await events.emit(
                "web.request",
                {"method": method, "url": url, "status": out.get("status"), "error": out.get("error")},
            )
            return out

        ctx.tool_registry.register(self.manifest.name, _WEB_SEARCH_DEF, _search)
        ctx.tool_registry.register(self.manifest.name, _WEB_FETCH_DEF, _fetch)
        ctx.tool_registry.register(self.manifest.name, _HTTP_REQUEST_DEF, _http)
        log.info("web_access.tools_registered", extra={"tools": ["web_search", "web_fetch", "http_request"]})
