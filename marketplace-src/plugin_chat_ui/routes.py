"""plugin-chat-ui routes — serves the compiled chat bundle.

The shell (Shell.tsx RemoteChatPanel) loads:

    /api/p/plugin-chat-ui/ui/chat.js?v={plugin version}
    /api/p/plugin-chat-ui/ui/chat.css?v={plugin version}

`?v=` is the cache-buster (the shell passes this plugin's version from the
/api/ui/plugins registry), so versioned asset responses are immutable and a
plugin upgrade busts browser/edge caches by construction. All chat data flows
through core APIs (/api/conversations*, /api/events) — this router only ships
static files.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response


def register_routes(app, ctx):
    router = APIRouter(prefix="/api/p/plugin-chat-ui", tags=["chat-ui"])

    ui_dir = Path(__file__).parent / "ui"

    _NO_CACHE = {"Cache-Control": "no-cache"}
    _IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

    @router.get("/ui/")
    async def serve_ui_root():
        built = (ui_dir / "chat.js").exists()
        return Response(
            content=f"<h1>plugin-chat-ui</h1><p>bundle {'present' if built else 'NOT BUILT'}</p>",
            media_type="text/html",
            headers=_NO_CACHE,
        )

    @router.get("/ui/{path:path}")
    async def serve_ui(path: str, v: str | None = None):
        target = (ui_dir / path).resolve()
        if not str(target).startswith(str(ui_dir.resolve())):
            raise HTTPException(403, "Forbidden")
        if not target.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(str(target), headers=_IMMUTABLE if v else _NO_CACHE)

    app.include_router(router)
