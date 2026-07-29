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
4. **reviews** (009) — the Marketplace pane inside Luna reads and writes
   reviews over the same signed channel. The install *is* the author: no
   marketplace account, no redirect to a website that doesn't know the user.
   Every write is signed with the install secret, so an install can only ever
   speak for itself.

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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import PUBLIC_BASE_URL, get_current_user
from ..database import get_db
from ..models.db import LunaInstall, Marketplace, OrgMember, Plugin, Review, ReviewVote, User, now_ts
from ..models.schemas import (
    LinkLunaRequest,
    LunaEnrollRequest,
    LunaEnrollResponse,
    LunaReviewCreate,
    ReviewSummary,
)

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


# --- Reviews written from inside Luna (009) --------------------------------
#
# The pane never redirects to the website. It POSTs here with the install's
# HMAC, and the install row is the author. Reads go through the same signed
# route so the pane can tell the caller which review is theirs.

DAILY_REVIEW_LIMIT = 10


async def _resolve_plugin(mp_slug: str, plugin_name: str, db: AsyncSession):
    mp = (
        await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    ).scalar_one_or_none()
    if mp is None:
        raise HTTPException(404, "Marketplace not found")
    plugin = (
        await db.execute(
            select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == plugin_name)
        )
    ).scalar_one_or_none()
    if plugin is None:
        raise HTTPException(404, "Plugin not found")
    return mp, plugin


async def _signed_body(request, tenant, ts, signature, db) -> tuple[LunaInstall, dict]:
    import json

    inst, raw = await _verify_signed(request, tenant, ts, signature, db)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(400, "body must be an object")
    return inst, body


@router.post("/luna/reviews/{mp_slug}/{plugin_name}/list")
async def luna_list_reviews(
    mp_slug: str,
    plugin_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_luna_tenant: str = Header(...),
    x_luna_ts: str = Header(...),
    x_luna_signature: str = Header(...),
):
    """Reviews for a plugin, plus which one belongs to the calling install.

    Body: {"sort": "helpful"|"recent"|"critical"}. POST (not GET) because the
    signature covers the raw body.
    """
    from .reviews import review_author

    inst, body = await _signed_body(request, x_luna_tenant, x_luna_ts, x_luna_signature, db)
    _mp, plugin = await _resolve_plugin(mp_slug, plugin_name, db)
    sort = str(body.get("sort") or "helpful")

    query = select(Review, User.username).outerjoin(User, Review.user_id == User.id).where(
        Review.plugin_id == plugin.id
    )
    if sort == "recent":
        query = query.order_by(Review.created_at.desc())
    elif sort == "critical":
        query = query.order_by(Review.rating.asc(), Review.helpful_count.desc())
    else:
        query = query.order_by(Review.helpful_count.desc(), Review.created_at.desc())
    rows = (await db.execute(query.limit(50))).all()

    hist_rows = (
        await db.execute(
            select(Review.rating, func.count(Review.id))
            .where(Review.plugin_id == plugin.id)
            .group_by(Review.rating)
        )
    ).all()
    histogram = {star: 0 for star in range(1, 6)}
    for star, count in hist_rows:
        histogram[int(star)] = int(count)

    mine = next((r.id for r, _ in rows if r.luna_install_id == inst.id), None)
    return {
        "summary": ReviewSummary(
            average=plugin.rating_average or 0.0,
            count=plugin.rating_count or 0,
            histogram=histogram,
        ),
        "reviews": [
            {
                "id": r.id,
                "rating": r.rating,
                "title": r.title or "",
                "body": r.body or "",
                "author": review_author(r, username),
                "plugin_version": r.plugin_version or "",
                "helpful_count": r.helpful_count or 0,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
                "edited": bool(r.edited),
                "response_body": r.response_body,
                "verified_install": bool(r.verified_install),
                "is_mine": r.luna_install_id == inst.id,
            }
            for r, username in rows
        ],
        "mine": mine,
        "can_review": True,
        "author_default": inst.luna_name or "",
    }


@router.post("/luna/reviews/{mp_slug}/{plugin_name}")
async def luna_write_review(
    mp_slug: str,
    plugin_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_luna_tenant: str = Header(...),
    x_luna_ts: str = Header(...),
    x_luna_signature: str = Header(...),
):
    """Create or replace this install's review of a plugin."""
    from .reviews import _recompute_rating

    inst, body = await _signed_body(request, x_luna_tenant, x_luna_ts, x_luna_signature, db)
    data = LunaReviewCreate(**body)
    mp, plugin = await _resolve_plugin(mp_slug, plugin_name, db)

    if data.rating <= 2 and not data.body.strip():
        raise HTTPException(400, "A written explanation is required for 1-2 star ratings")
    # A linked install still can't review its own org's plugin.
    if inst.user_id:
        member = await db.execute(
            select(OrgMember.id)
            .where(OrgMember.org_id == mp.org_id, OrgMember.user_id == inst.user_id)
            .limit(1)
        )
        if member.scalar_one_or_none() is not None:
            raise HTTPException(403, "Members of the publishing account cannot review their own plugin")

    day_ago = now_ts() - 86400
    recent = await db.execute(
        select(func.count(Review.id)).where(
            Review.luna_install_id == inst.id, Review.updated_at >= day_ago
        )
    )
    if int(recent.scalar() or 0) >= DAILY_REVIEW_LIMIT:
        raise HTTPException(429, "Review rate limit reached (10/day)")

    author = (data.author or inst.luna_name or "").strip() or "Luna owner"
    existing = (
        await db.execute(
            select(Review).where(
                Review.plugin_id == plugin.id, Review.luna_install_id == inst.id
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.rating = data.rating
        existing.title = data.title
        existing.body = data.body
        existing.author_display = author
        existing.plugin_version = data.plugin_version or plugin.latest_version or ""
        existing.verified_install = bool(data.plugin_version)
        existing.updated_at = now_ts()
        existing.edited = True
        review = existing
    else:
        review = Review(
            id=str(uuid.uuid4()),
            plugin_id=plugin.id,
            user_id=None,
            luna_install_id=inst.id,
            rating=data.rating,
            title=data.title,
            body=data.body,
            author_display=author,
            plugin_version=data.plugin_version or plugin.latest_version or "",
            verified_install=bool(data.plugin_version),
        )
        db.add(review)
        await db.flush()

    await _recompute_rating(db, plugin)
    await db.commit()
    return {
        "status": "ok",
        "review_id": review.id,
        "rating_average": plugin.rating_average,
        "rating_count": plugin.rating_count,
    }


@router.post("/luna/reviews/{mp_slug}/{plugin_name}/delete")
async def luna_delete_review(
    mp_slug: str,
    plugin_name: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_luna_tenant: str = Header(...),
    x_luna_ts: str = Header(...),
    x_luna_signature: str = Header(...),
):
    """Delete this install's own review. An install can only delete its own."""
    from .reviews import _recompute_rating

    inst, _body = await _signed_body(request, x_luna_tenant, x_luna_ts, x_luna_signature, db)
    _mp, plugin = await _resolve_plugin(mp_slug, plugin_name, db)
    review = (
        await db.execute(
            select(Review).where(
                Review.plugin_id == plugin.id, Review.luna_install_id == inst.id
            )
        )
    ).scalars().first()
    if review is None:
        raise HTTPException(404, "No review from this install")
    votes = await db.execute(select(ReviewVote).where(ReviewVote.review_id == review.id))
    for v in votes.scalars():
        await db.delete(v)
    await db.delete(review)
    await db.flush()
    await _recompute_rating(db, plugin)
    await db.commit()
    return {"status": "deleted"}


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
