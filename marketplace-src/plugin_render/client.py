"""Async HTTP client wrapper for the Render.com API (v1)."""

from __future__ import annotations

from typing import Any

import httpx

API_BASE = "https://api.render.com/v1"


class RenderClient:
    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url or API_BASE,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )

    # ── services ──────────────────────────────────────────────

    async def list_services(self) -> list[dict[str, Any]]:
        resp = await self._http.get("/services")
        resp.raise_for_status()
        return resp.json()

    async def get_service(self, service_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/services/{service_id}")
        resp.raise_for_status()
        return resp.json()

    async def restart_service(self, service_id: str) -> dict[str, Any]:
        resp = await self._http.post(f"/services/{service_id}/restart")
        resp.raise_for_status()
        return resp.json()

    async def suspend_service(self, service_id: str) -> dict[str, Any]:
        resp = await self._http.post(f"/services/{service_id}/suspend")
        resp.raise_for_status()
        return resp.json()

    async def resume_service(self, service_id: str) -> dict[str, Any]:
        resp = await self._http.post(f"/services/{service_id}/resume")
        resp.raise_for_status()
        return resp.json()

    async def get_live_logs(self, service_id: str) -> list[dict[str, Any]]:
        resp = await self._http.get(f"/services/{service_id}/logs")
        resp.raise_for_status()
        return resp.json()

    # ── deploys ───────────────────────────────────────────────

    async def trigger_deploy(
        self, service_id: str, *, clear_cache: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if clear_cache:
            body["clearCache"] = "clear"
        resp = await self._http.post(f"/services/{service_id}/deploys", json=body)
        resp.raise_for_status()
        return resp.json()

    async def list_deploys(self, service_id: str) -> list[dict[str, Any]]:
        resp = await self._http.get(f"/services/{service_id}/deploys")
        resp.raise_for_status()
        return resp.json()

    async def get_deploy(self, service_id: str, deploy_id: str) -> dict[str, Any]:
        resp = await self._http.get(f"/services/{service_id}/deploys/{deploy_id}")
        resp.raise_for_status()
        return resp.json()

    # ── env vars ──────────────────────────────────────────────

    async def list_env_vars(self, service_id: str) -> list[dict[str, Any]]:
        resp = await self._http.get(f"/services/{service_id}/env-vars")
        resp.raise_for_status()
        return resp.json()

    async def set_env_var(
        self, service_id: str, key: str, value: str,
    ) -> dict[str, Any]:
        resp = await self._http.put(
            f"/services/{service_id}/env-vars/{key}",
            json={"value": value},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_env_var(self, service_id: str, key: str) -> None:
        resp = await self._http.delete(f"/services/{service_id}/env-vars/{key}")
        resp.raise_for_status()

    # ── lifecycle ─────────────────────────────────────────────

    async def close(self) -> None:
        await self._http.aclose()
