"""plugin-marketplace-ui routes — the rich Marketplace pane (iframe) + reviews.

Catalog/install/upgrade calls the pane makes still go to core
plugin-marketplace APIs at /api/p/plugin-marketplace/*; this router ships the
static SPA and one thing of its own: the **review proxy**.

Reviews are written from inside the pane, signed with this install's handshake
key (see reviews.py), so the owner is never bounced to a website that doesn't
know them. The browser never sees the tenant or the secret — it posts here,
and this process signs. Every proxied call is owner-authenticated and the
target host must be a marketplace the owner actually added.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import reviews as rv


class ReviewListBody(BaseModel):
    marketplace_url: str
    slug: str
    plugin: str
    sort: str = "helpful"


class ReviewWriteBody(BaseModel):
    marketplace_url: str
    slug: str
    plugin: str
    rating: int
    title: str = ""
    body: str = ""


class ReviewDeleteBody(BaseModel):
    marketplace_url: str
    slug: str
    plugin: str


def register_routes(app, ctx):
    from luna.auth import get_current_user

    router = APIRouter(prefix="/api/p/plugin-marketplace-ui", tags=["marketplace-ui"])

    ui_dir = Path(__file__).parent / "ui"

    async def _check_origin(marketplace_url: str) -> None:
        """Only marketplaces the owner added may be signed for."""
        try:
            origin = rv.origin_of(marketplace_url)
        except rv.ReviewError as e:
            raise HTTPException(400, str(e)) from e
        allowed = await rv.trusted_origins(ctx)
        if allowed and origin not in allowed:
            raise HTTPException(403, "That marketplace isn't in this Luna's list.")

    def _fail(e: rv.ReviewError):
        return HTTPException(e.status if 400 <= e.status < 600 else 502, str(e))

    @router.post("/reviews/list")
    async def reviews_list(body: ReviewListBody, user=Depends(get_current_user)):
        """Reviews for a plugin, read over the signed channel so the response
        can say which one is ours."""
        await _check_origin(body.marketplace_url)
        path = f"/api/luna/reviews/{body.slug}/{body.plugin}/list"
        try:
            return await rv.signed_call(ctx, body.marketplace_url, path, {"sort": body.sort})
        except rv.ReviewError as e:
            raise _fail(e) from e

    @router.post("/reviews/write")
    async def reviews_write(body: ReviewWriteBody, user=Depends(get_current_user)):
        """Create or replace this install's review."""
        await _check_origin(body.marketplace_url)
        if not 1 <= body.rating <= 5:
            raise HTTPException(400, "Rating must be 1-5.")
        path = f"/api/luna/reviews/{body.slug}/{body.plugin}"
        payload = {
            "rating": body.rating,
            "title": body.title[:80],
            "body": body.body[:2000],
            "author": getattr(user, "username", "") or "",
            "plugin_version": rv.installed_version(body.plugin),
        }
        try:
            return await rv.signed_call(ctx, body.marketplace_url, path, payload)
        except rv.ReviewError as e:
            raise _fail(e) from e

    @router.post("/reviews/delete")
    async def reviews_delete(body: ReviewDeleteBody, user=Depends(get_current_user)):
        await _check_origin(body.marketplace_url)
        path = f"/api/luna/reviews/{body.slug}/{body.plugin}/delete"
        try:
            return await rv.signed_call(ctx, body.marketplace_url, path, {})
        except rv.ReviewError as e:
            raise _fail(e) from e

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
