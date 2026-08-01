"""plugin-mcp API routes (008.5/phase12).

NOTE: `from __future__ import annotations` is intentionally absent. It
stringifies Pydantic model hints in FastAPI handler signatures, which
prevents the body-parameter resolution needed by MCPAddRequest et al.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from .schemas import (
    MCPAddRequest,
    MCPServerInfo,
    MCPTestRequest,
    MCPTestResult,
    MCPToolInfo,
    MCPUpdateRequest,
)
from .state import get_manager

_SETTINGS_DIR = Path(__file__).parent / "interface" / "webui" / "settings"


def register_routes(app, ctx):
    from luna_sdk import get_current_user

    router = APIRouter(prefix="/api/p/plugin-mcp", tags=["mcp"])

    def _mgr():
        return get_manager()

    # 068/phase003: re-boot MCP servers once the REAL event loop is running.
    # Plugin on_load runs on a throwaway bootstrap loop (luna_serve's
    # asyncio.run); clients connected there are zombies — every call returns
    # server_offline. Mirrors plugin_connectors' startup restore. boot() is
    # loop-aware, so on the `luna serve` CLI path (which already re-boots via
    # cli.py) this is a no-op instead of a double boot.
    async def _reboot_on_startup() -> None:
        import asyncio
        import logging

        log = logging.getLogger("plugin-mcp.routes")

        async def _go() -> None:
            mgr = get_manager()
            if mgr is None:
                return
            if getattr(mgr, "_boot_loop_id", None) == id(asyncio.get_running_loop()):
                return  # already booted on this loop
            # Zombie clients exist only on the ASGI path (on_load's bootstrap
            # loop died with them). The CLI path shuts down in its bootstrap
            # loop, so the dict is empty there and we skip straight to boot —
            # never racing cli.py's own startup reboot with a shutdown.
            if mgr._clients:
                try:
                    await mgr.shutdown()  # best-effort: reap bootstrap-loop zombies
                except Exception as e:  # noqa: BLE001
                    log.warning("mcp startup pre-shutdown failed: %s", e)
            try:
                await mgr.boot()
            except Exception as e:  # noqa: BLE001
                log.warning("mcp startup boot failed: %s", e)

        asyncio.create_task(_go())

    # FastAPI ≥0.136 dropped add_event_handler; the Starlette router list remains.
    app.router.on_startup.append(_reboot_on_startup)

    @router.get("/servers", response_model=list[MCPServerInfo])
    async def list_servers(user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            return []
        return await mgr.list_servers()

    @router.post("/servers", response_model=MCPServerInfo)
    async def add_server(req: MCPAddRequest, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        try:
            return await mgr.add(req.name, req.transport_type, req.config, enable=req.enable)
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @router.put("/servers/{name}", response_model=MCPServerInfo)
    async def update_server(name: str, req: MCPUpdateRequest, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        try:
            if req.config is not None:
                await mgr.update_config(name, config=req.config)
            if req.enabled is True:
                await mgr.enable(name)
            elif req.enabled is False:
                await mgr.disable(name)
            return await mgr.get(name)
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @router.delete("/servers/{name}")
    async def delete_server(name: str, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        try:
            await mgr.remove(name)
        except Exception as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @router.post("/servers/{name}/refresh", response_model=MCPServerInfo)
    async def refresh_server(name: str, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        try:
            return await mgr.refresh(name)
        except Exception as e:
            raise HTTPException(400, str(e)) from e

    @router.post("/test", response_model=MCPTestResult)
    async def test_config(req: MCPTestRequest, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        return await mgr.test_config(req.transport_type, req.config)

    @router.get("/servers/{name}/tools", response_model=list[MCPToolInfo])
    async def get_tools(name: str, user=Depends(get_current_user)):
        mgr = _mgr()
        if mgr is None:
            raise HTTPException(503, "plugin-mcp not loaded")
        try:
            return await mgr.get_tools(name)
        except Exception as e:
            raise HTTPException(404, str(e)) from e

    # --- Settings UI (served as a themed iframe by the host) ---

    @router.get("/ui/settings/")
    async def settings_index():
        index = _SETTINGS_DIR / "index.html"
        if not index.exists():
            raise HTTPException(404, "settings UI not found")
        return FileResponse(str(index), headers={"Cache-Control": "no-cache"})

    @router.get("/ui/settings/{path:path}")
    async def settings_asset(path: str):
        target = (_SETTINGS_DIR / path).resolve()
        if not str(target).startswith(str(_SETTINGS_DIR.resolve())):
            raise HTTPException(403, "forbidden")
        if not target.exists() or target.is_dir():
            return FileResponse(str(_SETTINGS_DIR / "index.html"), headers={"Cache-Control": "no-cache"})
        return FileResponse(str(target), headers={"Cache-Control": "no-cache"})

    app.include_router(router)
