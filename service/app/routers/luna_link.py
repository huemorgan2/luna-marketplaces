"""Luna ↔ marketplace handshake.

Flow (enroll → HMAC, mirroring linear-ascent's worldd auth):

1. **enroll** — a Luna install sends its vault-stored `install_id`. First
   enroll creates a `luna_installs` row and returns a `secret` (shown exactly
   once; Luna stores it in its vault) plus a short-lived `link_code`.
2. **link** — a signed-in marketplace user submits the code on the website
   (`POST /api/me/link-luna`). The install binds to the user and the user
   becomes **certified** (may write reviews).
3. **sync** — Luna periodically pushes its installed-plugin list, signed
   `HMAC_SHA256(secret, f"{ts}.{raw_body}")` with headers
   `X-Luna-Tenant / X-Luna-Ts / X-Luna-Signature` (±300s skew).

Certification asserts only "this account operates a real Luna install" —
Luna has no user email, so no identity beyond the install is claimed.
Secrets are never logged and never re-issued.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets as pysecrets
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import PUBLIC_BASE_URL, get_current_user
from ..database import get_db
from ..models.db import LunaInstall, User, now_ts
from ..models.schemas import LinkLunaRequest, LunaEnrollRequest, LunaEnrollResponse

router = APIRouter()

SKEW_SECONDS = 300
LINK_CODE_TTL = 15 * 60
# Unambiguous alphabet (no 0/O/1/I) for codes typed by humans.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _new_link_code() -> str:
    return "".join(pysecrets.choice(_CODE_ALPHABET) for _ in range(8))


def _link_url(code: str) -> str:
    base = PUBLIC_BASE_URL or ""
    return f"{base}/link?code={code}"


@router.post("/luna/enroll", response_model=LunaEnrollResponse)
async def enroll(data: LunaEnrollRequest, db: AsyncSession = Depends(get_db)):
    """Register (or re-greet) a Luna install. The secret is returned only on
    first enroll; re-enrolls of an unlinked install refresh the link code."""
    result = await db.execute(select(LunaInstall).where(LunaInstall.install_id == data.install_id))
    inst = result.scalar_one_or_none()

    if inst is None:
        inst = LunaInstall(
            id=str(uuid.uuid4()),
            install_id=data.install_id,
            secret=pysecrets.token_hex(32),
            luna_name=data.luna_name or "",
            luna_version=data.luna_version or "",
            base_url=data.base_url,
            link_code=_new_link_code(),
            link_code_expires=now_ts() + LINK_CODE_TTL,
        )
        db.add(inst)
        await db.commit()
        return LunaEnrollResponse(
            tenant=inst.id,
            secret=inst.secret,
            link_code=inst.link_code,
            link_url=_link_url(inst.link_code),
            linked=False,
            existing=False,
        )

    # Existing install: never re-issue the secret.
    inst.luna_name = data.luna_name or inst.luna_name
    inst.luna_version = data.luna_version or inst.luna_version
    if data.base_url:
        inst.base_url = data.base_url
    linked = inst.user_id is not None
    if not linked and (not inst.link_code or inst.link_code_expires < now_ts()):
        inst.link_code = _new_link_code()
        inst.link_code_expires = now_ts() + LINK_CODE_TTL
    await db.commit()
    return LunaEnrollResponse(
        tenant=inst.id,
        secret=None,
        link_code=None if linked else inst.link_code,
        link_url="" if linked else _link_url(inst.link_code),
        linked=linked,
        existing=True,
    )


async def _verify_signed(
    request: Request,
    tenant: str,
    ts: str,
    signature: str,
    db: AsyncSession,
) -> tuple[LunaInstall, bytes]:
    inst = await db.get(LunaInstall, tenant)
    if inst is None:
        raise HTTPException(401, "unknown tenant")
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(401, "bad timestamp")
    if abs(now_ts() - ts_int) > SKEW_SECONDS:
        raise HTTPException(401, "timestamp outside allowed skew")
    raw = await request.body()
    expected = hmac.new(inst.secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(401, "bad signature")
    return inst, raw


@router.post("/luna/sync")
async def sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_luna_tenant: str = Header(...),
    x_luna_ts: str = Header(...),
    x_luna_signature: str = Header(...),
):
    """Signed push of the install's plugin list.

    Body: {"installed":[{"name","version","settings":bool}], "base_url", "luna_version"}
    """
    inst, raw = await _verify_signed(request, x_luna_tenant, x_luna_ts, x_luna_signature, db)

    import json

    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "invalid JSON body")

    installed = body.get("installed")
    if not isinstance(installed, list):
        raise HTTPException(400, "installed must be a list")
    clean: list[dict] = []
    for item in installed[:500]:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        clean.append({
            "name": str(item["name"])[:200],
            "version": str(item.get("version", ""))[:50],
            "settings": bool(item.get("settings", False)),
        })
    inst.installed = clean
    if body.get("base_url"):
        inst.base_url = str(body["base_url"])[:500]
    if body.get("luna_version"):
        inst.luna_version = str(body["luna_version"])[:50]
    inst.last_sync_at = now_ts()
    await db.commit()
    return {"status": "ok", "installed_count": len(clean), "linked": inst.user_id is not None}


@router.post("/me/link-luna")
async def link_luna(
    data: LinkLunaRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind a Luna install to the signed-in user via its link code → certified."""
    code = data.code.strip().upper()
    result = await db.execute(select(LunaInstall).where(LunaInstall.link_code == code))
    inst = result.scalar_one_or_none()
    if inst is None or inst.link_code_expires < now_ts():
        raise HTTPException(404, "Invalid or expired link code. Get a fresh one from Luna.")
    if inst.user_id is not None and inst.user_id != user.id:
        raise HTTPException(409, "This Luna install is already linked to another account")

    inst.user_id = user.id
    inst.linked_at = now_ts()
    inst.link_code = None
    inst.link_code_expires = 0
    if not user.certified_at:
        user.certified_at = now_ts()
    await db.commit()
    return {
        "status": "linked",
        "install": {"id": inst.id, "name": inst.luna_name, "version": inst.luna_version},
        "certified_at": user.certified_at,
    }


@router.get("/me/luna-installs")
async def my_luna_installs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(LunaInstall).where(LunaInstall.user_id == user.id))
    installs = result.scalars().all()
    return {
        "certified_at": user.certified_at,
        "installs": [
            {
                "id": i.id,
                "name": i.luna_name,
                "version": i.luna_version,
                "base_url": i.base_url,
                "installed_count": len(i.installed or []),
                "linked_at": i.linked_at,
                "last_sync_at": i.last_sync_at,
            }
            for i in installs
        ],
    }


@router.delete("/me/luna-installs/{install_row_id}")
async def unlink_luna(
    install_row_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unlink an install from the account. Losing the last install clears
    certification (existing reviews stay)."""
    inst = await db.get(LunaInstall, install_row_id)
    if inst is None or inst.user_id != user.id:
        raise HTTPException(404, "Install not found")
    await db.delete(inst)
    await db.flush()
    remaining = await db.execute(
        select(LunaInstall.id).where(LunaInstall.user_id == user.id).limit(1)
    )
    if remaining.scalar_one_or_none() is None:
        user.certified_at = None
    await db.commit()
    return {"status": "unlinked", "certified": user.certified_at is not None}


@router.get("/me/installed")
async def my_installed(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Union of installed plugins across the user's linked Lunas, for the
    website's Installed pills and settings deep-links."""
    result = await db.execute(select(LunaInstall).where(LunaInstall.user_id == user.id))
    installs = result.scalars().all()
    installed: dict[str, str] = {}
    settings: dict[str, bool] = {}
    base_url: str | None = None
    for i in installs:
        if i.base_url:
            base_url = i.base_url
        for item in i.installed or []:
            installed[item["name"]] = item.get("version", "")
            if item.get("settings"):
                settings[item["name"]] = True
    return {"installed": installed, "settings": settings, "luna_base_url": base_url}
