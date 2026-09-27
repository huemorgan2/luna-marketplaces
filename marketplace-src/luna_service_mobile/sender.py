"""Batches feed events and POSTs them to the control plane.

One task drains a bounded queue: it waits for the first item, gives the rest of a burst a moment
to arrive, resolves each item into feed events (the resolver may read Luna's DB), and POSTs them
to `{LUNA_GATEWAY_URL}/api/agent/chats/events`. Failures back off and retry the same batch; a 401
(token rotated) drops it. Nothing here ever raises into Luna.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

log = logging.getLogger("luna-service-mobile.sender")

PATH = "/api/agent/chats/events"
MAX_QUEUE = 2000
MAX_BATCH = 200
BURST_WAIT = 0.3
MAX_BACKOFF = 60.0

Resolver = Callable[[list[Any]], Awaitable[list[dict]]]
Post = Callable[[list[dict]], Awaitable[int]]


class Sender:
    def __init__(self, base_url: str, token: str, resolve: Resolver, post: Post | None = None):
        self._url = base_url.rstrip("/") + PATH
        self._token = token
        self._resolve = resolve
        self._post = post or self._http_post
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE)
        self._task: asyncio.Task | None = None
        self._dropped = 0

    def put(self, item: Any) -> None:
        """Called from bus handlers: never blocks, never raises."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            # Oldest-first drop keeps the newest state, which is what the feed shows.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(item)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("feed queue full; dropped %d item(s) so far", self._dropped)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def drain_once(self) -> None:
        """Send whatever is queued right now (tests, shutdown)."""
        items = []
        while not self._queue.empty() and len(items) < MAX_BATCH:
            items.append(self._queue.get_nowait())
        if items:
            await self._send(items)

    async def _run(self) -> None:
        while True:
            try:
                items = [await self._queue.get()]
                await asyncio.sleep(BURST_WAIT)
                while not self._queue.empty() and len(items) < MAX_BATCH:
                    items.append(self._queue.get_nowait())
                await self._send(items)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("feed sender loop error")
                await asyncio.sleep(1)

    async def _send(self, items: list[Any]) -> None:
        try:
            events = await self._resolve(items)
        except Exception:  # noqa: BLE001
            log.exception("feed resolve failed; skipping %d item(s)", len(items))
            return
        if not events:
            return
        backoff = 1.0
        while True:
            try:
                status = await self._post(events)
            except Exception as exc:  # noqa: BLE001
                status = 0
                log.info("feed post failed: %s", exc)
            if 200 <= status < 300:
                return
            if status in (400, 401, 403, 404, 422):
                # Not something a retry fixes (token rotated, route missing on an old control plane).
                log.warning("feed post rejected with %s; dropping %d event(s)", status, len(events))
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _http_post(self, events: list[dict]) -> int:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"events": events},
            )
        return resp.status_code
