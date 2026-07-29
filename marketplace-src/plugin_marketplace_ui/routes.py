"""plugin-marketplace-ui routes — serves the rich Marketplace pane (iframe).

UI only: every catalog/install/upgrade call the pane makes goes to core
plugin-marketplace APIs at /api/p/plugin-marketplace/*. This router only
ships the static SPA.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response


def register_routes(app, ctx):
    router = APIRouter(prefix="/api/p/plugin-marketplace-ui", tags=["marketplace-ui"])

    ui_dir = Path(__file__).parent / "ui"

    # Same cache discipline as core's pane: index.html is never cached hard,
    # and it stamps the plugin version onto asset URLs so every upgrade of
    # this plugin busts edge/browser caches of app.js/style.css.
    _NO_CACHE = {"Cache-Control": "no-cache"}
    _IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

    def _plugin_version() -> str:
        from . import __version__

        return __version__

    def _versioned_index() -> Response:
        v = _plugin_version()
        html = (ui_dir / "index.html").read_text()
        html = html.replace('href="style.css"', f'href="style.css?v={v}"')
        html = html.replace('src="app.js"', f'src="app.js?v={v}"')
        html = html.replace("__MP_UI_VERSION__", v)
        return Response(content=html, media_type="text/html", headers=_NO_CACHE)

    @router.get("/ui/")
    async def serve_ui_root():
        if (ui_dir / "index.html").exists():
            return _versioned_index()
        return Response(content="<h1>plugin-marketplace-ui not built</h1>", media_type="text/html")

    @router.get("/ui/{path:path}")
    async def serve_ui(path: str, v: str | None = None):
        if not path or path == "/":
            path = "index.html"
        target = (ui_dir / path).resolve()
        if not str(target).startswith(str(ui_dir.resolve())):
            raise HTTPException(403, "Forbidden")
        if not target.exists() or target.name == "index.html":
            if (ui_dir / "index.html").exists():
                return _versioned_index()
            raise HTTPException(404, "Not found")
        return FileResponse(str(target), headers=_IMMUTABLE if v else _NO_CACHE)

    app.include_router(router)
