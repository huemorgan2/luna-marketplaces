"""068/phase002: one shared httpx.AsyncClient per event loop for web tools.

Every web_search / web_fetch / http_request call used to build (and tear
down) a fresh AsyncClient — a cold TCP+TLS handshake per tool call, ~100-300
ms to Tavily/Google and any fetched site, plus fd churn. All three tools now
default to this per-loop singleton; per-call needs (custom timeout) are passed
per-request, which httpx supports. The `client=` injection seam for tests is
unchanged: an injected client is used as-is and never closed.

Keyed by running event loop: httpx pools bind sockets to the loop that opened
them, and plugins load on a throwaway boot loop before uvicorn's real one.
"""

from __future__ import annotations

import asyncio

import httpx

USER_AGENT = "Luna/1.0 (AI Agent; +https://github.com/huemorgan/luna)"
DEFAULT_TIMEOUT = 30.0

_clients: dict[int, httpx.AsyncClient] = {}


def shared_client() -> httpx.AsyncClient:
    """The per-event-loop shared client (created lazily, reused across calls)."""
    loop_id = id(asyncio.get_running_loop())
    client = _clients.get(loop_id)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": USER_AGENT},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        _clients[loop_id] = client
    return client


async def aclose_shared_clients() -> None:
    """Close every cached client (app shutdown / test teardown)."""
    clients = list(_clients.values())
    _clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001 — client may belong to a dead loop
            pass
