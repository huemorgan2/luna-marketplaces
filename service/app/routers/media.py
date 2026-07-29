"""Plugin media: content-addressed bytes served at /media/{sha256}.

Bytes live in the same artifact store as plugin zips but under a `.bin`
extension so the namespaces never collide. Rows live in `plugin_media`.
Upload via the dashboard/API; official plugins are also seeded from
`marketplace-src/<pkg>/media/` (see seed_core).
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import storage
from ..auth import get_current_user
from ..database import get_db
from ..models.db import PluginMedia, User
from .plugins import _get_plugin_for_editor

router = APIRouter()

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml", "video/mp4"}
MAX_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 10
KINDS = {"icon", "cover", "screenshot"}


@router.get("/media/{sha256}")
async def serve_media(sha256: str, db: AsyncSession = Depends(get_db)):
    """Serve media bytes. Content-addressed → immutable cache."""
    row = (
        await db.execute(select(PluginMedia).where(PluginMedia.sha256 == sha256).limit(1))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "media not found")
    try:
        data = storage.read(sha256, ext=".bin")
    except FileNotFoundError:
        raise HTTPException(410, "media bytes missing on disk")
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post("/api/marketplaces/{mp_slug}/plugins/{plugin_name}/media")
async def upload_media(
    mp_slug: str,
    plugin_name: str,
    file: UploadFile = File(...),
    kind: str = Form("screenshot"),
    caption: str = Form(""),
    sort_order: int = Form(0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _mp, plugin = await _get_plugin_for_editor(mp_slug, plugin_name, user, db)

    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {sorted(KINDS)}")
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"unsupported content type {content_type}")
    data = await file.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(400, "media file exceeds 8 MB")

    existing = (
        await db.execute(select(PluginMedia).where(PluginMedia.plugin_id == plugin.id))
    ).scalars().all()
    if len(existing) >= MAX_ITEMS:
        raise HTTPException(400, f"a plugin can have at most {MAX_ITEMS} media items")

    sha256 = hashlib.sha256(data).hexdigest()
    storage.store(sha256, data, ext=".bin")

    # icon/cover are singular: replace any previous row of the same kind.
    if kind in ("icon", "cover"):
        for m in existing:
            if m.kind == kind:
                await db.delete(m)

    row = PluginMedia(
        id=str(uuid.uuid4()),
        plugin_id=plugin.id,
        kind=kind,
        sha256=sha256,
        content_type=content_type,
        caption=caption,
        sort_order=sort_order,
    )
    db.add(row)
    await db.commit()
    return {"status": "uploaded", "sha256": sha256, "url": f"/media/{sha256}", "kind": kind}


@router.delete("/api/marketplaces/{mp_slug}/plugins/{plugin_name}/media/{media_id}")
async def delete_media(
    mp_slug: str,
    plugin_name: str,
    media_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _mp, plugin = await _get_plugin_for_editor(mp_slug, plugin_name, user, db)
    row = await db.get(PluginMedia, media_id)
    if row is None or row.plugin_id != plugin.id:
        raise HTTPException(404, "media not found")
    sha256 = row.sha256
    await db.delete(row)
    await db.flush()
    still = (
        await db.execute(select(PluginMedia.id).where(PluginMedia.sha256 == sha256).limit(1))
    ).scalar_one_or_none()
    await db.commit()
    if still is None:
        storage.delete(sha256, ext=".bin")
    return {"status": "deleted", "media_id": media_id}
