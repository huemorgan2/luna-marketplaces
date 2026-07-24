"""Luna Marketplaces Service — main FastAPI application."""

from __future__ import annotations

import io
import logging
import os
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import init_db
from .routers.bundles import router as bundles_router
from .routers.core import router as core_router
from .routers.plugins import router as plugins_router
from .routers.registry import router as registry_router
from .seed_core import seed_core_plugins

STATIC_DIR = Path(__file__).parent.parent / "static"
TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

# The downloadable plugin dev kit. Repo root locally; /app/luna-plugin-dev-kit in Docker.
DEV_KIT_DIR = Path(
    os.environ.get("DEV_KIT_DIR", str(Path(__file__).parent.parent.parent / "luna-plugin-dev-kit"))
)

logger = logging.getLogger("luna.marketplaces")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    try:
        for line in await seed_core_plugins():
            logger.info("seed: %s", line)
    except Exception:  # noqa: BLE001 — never block boot on seeding
        logger.exception("core plugin seeding failed")
    yield


app = FastAPI(
    title="Luna Marketplaces",
    description="Plugin marketplace service for the Luna agent platform",
    version="0.2.0",
    lifespan=lifespan,
)

app.include_router(core_router, prefix="/api")
# bundles before plugins: /catalog/{slug}/bundles must win over /catalog/{slug}/{plugin_name}
app.include_router(bundles_router, prefix="/api")
app.include_router(plugins_router, prefix="/api")
app.include_router(registry_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="app.html")


@app.get("/browse/{mp_slug}", response_class=HTMLResponse)
async def marketplace_catalog(request: Request, mp_slug: str):
    return templates.TemplateResponse(request=request, name="catalog.html")


@app.get("/browse/{mp_slug}/plugin/{plugin_name}", response_class=HTMLResponse)
async def plugin_detail(request: Request, mp_slug: str, plugin_name: str):
    return templates.TemplateResponse(request=request, name="plugin_detail.html")


@app.get("/dev-kit.zip")
async def dev_kit():
    """Zip and serve the Luna Plugin Dev Kit on the fly (always fresh)."""
    if not DEV_KIT_DIR.is_dir():
        raise HTTPException(404, "dev kit not available")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(DEV_KIT_DIR.rglob("*")):
            if not p.is_file():
                continue
            if "__pycache__" in p.parts or p.suffix == ".pyc":
                continue
            # Extracts to a top-level `luna-plugin-dev-kit/` directory.
            z.write(p, p.relative_to(DEV_KIT_DIR.parent).as_posix())
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="luna-plugin-dev-kit.zip"'},
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "luna-marketplaces", "version": "0.2.0"}
