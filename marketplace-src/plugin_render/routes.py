"""plugin-render API routes — connect, disconnect, status, webhook, settings UI.

008.5/phase05: decoupled to the SDK surface.
- route-auth via `luna_sdk.get_current_user` (no `import luna.auth`)
- vault via `ctx.vault` (no `import luna.providers.vault`)
- live client swapped via the package's `state` module (no `get_plugin_registry`)
- settings UI served from `interface/webui/settings/` (works from a managed dir)
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from luna_sdk import get_current_user

from .client import RenderClient
from .state import get_client, set_client

log = logging.getLogger("plugin-render.routes")

VAULT_KEY = "plugin_render.api_key"
WEBHOOK_SECRET_KEY = "plugin_render.webhook_secret"

_SETTINGS_DIR = Path(__file__).parent / "interface" / "webui" / "settings"


class _ConnectReq(BaseModel):
    api_key: str
    base_url: str | None = None


class _StatusResp(BaseModel):
    connected: bool
    service_count: int | None = None


def register_routes(app, ctx):
    router = APIRouter(prefix="/api/p/plugin-render", tags=["render"])

    def _vault():
        vault = ctx.vault
        if vault is None:
            raise HTTPException(503, "Vault not available")
        return vault

    @router.post("/connect")
    async def connect(body: _ConnectReq, user=Depends(get_current_user)):
        vault = _vault()

        client = RenderClient(body.api_key, base_url=body.base_url)
        try:
            services = await client.list_services()
        except Exception as e:
            await client.close()
            raise HTTPException(400, f"Invalid API key: {e}") from e
        finally:
            await client.close()

        await vault.store_credential(VAULT_KEY, body.api_key, kind="api_key")
        set_client(RenderClient(body.api_key, base_url=body.base_url))

        return {"connected": True, "service_count": len(services)}

    @router.post("/disconnect")
    async def disconnect(user=Depends(get_current_user)):
        vault = _vault()
        await vault.delete_credential(VAULT_KEY)

        client = get_client()
        if client is not None:
            await client.close()
            set_client(None)

        return {"connected": False}

    @router.get("/status", response_model=_StatusResp)
    async def status(user=Depends(get_current_user)):
        vault = _vault()
        try:
            await vault.get_credential(VAULT_KEY)
        except KeyError:
            return _StatusResp(connected=False)

        count = None
        client = get_client()
        if client is not None:
            try:
                services = await client.list_services()
                count = len(services)
            except Exception:
                pass

        return _StatusResp(connected=True, service_count=count)

    @router.post("/webhook")
    async def webhook(request: Request, secret: str = Query(None)):
        vault = _vault()
        try:
            stored = await vault.get_credential(WEBHOOK_SECRET_KEY)
            expected_secret = stored.value
        except KeyError:
            expected_secret = None

        if expected_secret and secret != expected_secret:
            raise HTTPException(403, "Invalid webhook secret")

        payload = await request.json()
        event_type = payload.get("type", "")

        event_map = {
            "deploy_started": "render.deploy.started",
            "deploy_live": "render.deploy.succeeded",
            "deploy_failed": "render.deploy.failed",
            "server_suspended": "render.service.suspended",
            "server_resumed": "render.service.resumed",
        }

        bus_event = event_map.get(event_type)
        if bus_event:
            await ctx.events.emit(bus_event, payload)
            log.info("render webhook: %s", bus_event)
        else:
            log.info("render webhook: unknown type %s", event_type)

        return {"ok": True}

    # ── settings UI (served as a themed iframe by the host) ───────────
    # The host Settings panel loads /api/p/plugin-render/ui/settings/ in an
    # iframe (SettingsTab.iframe_src). Static, same-origin, so the page can call
    # the authed routes above. Files resolve relative to __file__ → works
    # identically in-tree and from the managed dir.

    @router.get("/ui/settings/")
    async def settings_index():
        index = _SETTINGS_DIR / "index.html"
        if not index.exists():
            raise HTTPException(404, "settings UI not found")
        return FileResponse(str(index), headers={"Cache-Control": "no-cache"})

    @router.get("/ui/settings/{path:path}")
    async def settings_asset(path: str):
        target = (_SETTINGS_DIR / path).resolve()
        # Guard against path traversal — must stay inside the settings dir.
        if not str(target).startswith(str(_SETTINGS_DIR.resolve())):
            raise HTTPException(403, "forbidden")
        if not target.exists() or target.is_dir():
            index = _SETTINGS_DIR / "index.html"
            return FileResponse(str(index), headers={"Cache-Control": "no-cache"})
        return FileResponse(str(target), headers={"Cache-Control": "no-cache"})

    app.include_router(router)
