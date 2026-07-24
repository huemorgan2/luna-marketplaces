"""API routes for accounts, orgs, and marketplaces."""

from __future__ import annotations

import secrets
import uuid
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    PUBLIC_BASE_URL,
    create_access_token,
    get_current_user,
    google_oauth_configured,
    hash_password,
    hash_publish_token,
    is_global_editor,
    new_publish_token,
    verify_password,
)
from ..database import get_db
from ..models.db import Marketplace, Org, OrgMember, Plugin, PublishToken, User, UsageEvent, now_ts
from ..models.schemas import (
    LoginRequest,
    MarketplaceCreate,
    MarketplaceResponse,
    MyMarketplaceResponse,
    OrgCreate,
    OrgResponse,
    PublishTokenCreated,
    PublishTokenInfo,
    TokenResponse,
    UserCreate,
    UserResponse,
)

router = APIRouter()


@router.post("/auth/signup", response_model=UserResponse)
async def signup(data: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.email == data.email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")

    user = User(
        id=str(uuid.uuid4()),
        email=data.email,
        username=data.username,
        password_hash=hash_password(data.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserResponse(id=user.id, email=user.email, username=user.username, created_at=user.created_at)


@router.post("/auth/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token(user.id)
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# Google OAuth (server-side authorization-code flow)
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _redirect_uri(request: Request) -> str:
    """The OAuth callback URI — must match what's registered in Google Cloud."""
    base = PUBLIC_BASE_URL or str(request.base_url).rstrip("/")
    return f"{base}/api/auth/google/callback"


async def _unique_username(db: AsyncSession, preferred: str) -> str:
    base = "".join(c for c in preferred.lower() if c.isalnum() or c in "-_") or "user"
    candidate = base
    i = 0
    while True:
        exists = await db.execute(select(User).where(User.username == candidate))
        if not exists.scalar_one_or_none():
            return candidate
        i += 1
        candidate = f"{base}{i}"


@router.get("/auth/google/login")
async def google_login(request: Request):
    """Kick off Google sign-in: redirect the browser to Google's consent page."""
    if not google_oauth_configured():
        raise HTTPException(503, "Google sign-in is not configured")

    state = secrets.token_urlsafe(24)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": _redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    resp = RedirectResponse(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")
    # CSRF: bind the state to this browser via an httponly cookie checked on callback.
    resp.set_cookie("g_oauth_state", state, max_age=600, httponly=True, samesite="lax", secure=True)
    return resp


@router.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google's redirect: exchange code, upsert user, return to app with a token."""
    if error:
        return RedirectResponse(f"/?auth_error={error}")
    if not google_oauth_configured():
        raise HTTPException(503, "Google sign-in is not configured")
    if not code:
        return RedirectResponse("/?auth_error=missing_code")

    cookie_state = request.cookies.get("g_oauth_state")
    if not state or not cookie_state or state != cookie_state:
        return RedirectResponse("/?auth_error=state_mismatch")

    redirect_uri = _redirect_uri(request)
    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code != 200:
            return RedirectResponse("/?auth_error=token_exchange_failed")
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse("/?auth_error=no_access_token")

        info_resp = await client.get(
            GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        if info_resp.status_code != 200:
            return RedirectResponse("/?auth_error=userinfo_failed")
        info = info_resp.json()

    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", True):
        return RedirectResponse("/?auth_error=email_unverified")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        username = await _unique_username(db, info.get("name") or email.split("@")[0])
        user = User(
            id=str(uuid.uuid4()),
            email=email,
            username=username,
            # No usable password for OAuth users; they sign in via Google only.
            password_hash=hash_password(secrets.token_urlsafe(32)),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    jwt_token = create_access_token(user.id)
    resp = RedirectResponse(f"/?token={jwt_token}")
    resp.delete_cookie("g_oauth_state")
    return resp


@router.get("/auth/config")
async def auth_config():
    """Lets the frontend know which auth methods are available."""
    return {"google": google_oauth_configured()}


@router.post("/orgs", response_model=OrgResponse)
async def create_org(
    data: OrgCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(Org).where(Org.slug == data.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Org slug already taken")

    org = Org(id=str(uuid.uuid4()), name=data.name, slug=data.slug)
    db.add(org)

    member = OrgMember(
        id=str(uuid.uuid4()), org_id=org.id, user_id=user.id, role="owner"
    )
    db.add(member)
    await db.commit()
    await db.refresh(org)
    return OrgResponse(id=org.id, name=org.name, slug=org.slug, plan=org.plan, created_at=org.created_at)


@router.get("/orgs", response_model=list[OrgResponse])
async def list_orgs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Org).join(OrgMember).where(OrgMember.user_id == user.id)
    )
    return [
        OrgResponse(id=o.id, name=o.name, slug=o.slug, plan=o.plan, created_at=o.created_at)
        for o in result.scalars()
    ]


@router.post("/orgs/{org_slug}/marketplaces", response_model=MarketplaceResponse)
async def create_marketplace(
    org_slug: str,
    data: MarketplaceCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _get_org_for_user(org_slug, user, db, min_role="owner")

    existing = await db.execute(select(Marketplace).where(Marketplace.slug == data.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Marketplace slug already taken")

    # Generate signing keypair
    sk = SigningKey.generate()
    pub_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    priv_hex = sk.encode(encoder=HexEncoder).decode()

    # Generate access token for private marketplaces
    access_token = secrets.token_urlsafe(32) if data.visibility == "private" else None

    mp = Marketplace(
        id=str(uuid.uuid4()),
        org_id=org.id,
        name=data.name,
        slug=data.slug,
        description=data.description,
        visibility=data.visibility,
        signing_key_public=pub_hex,
        signing_key_private_encrypted=priv_hex,
        access_token=access_token,
    )
    db.add(mp)
    await db.commit()
    await db.refresh(mp)

    return MarketplaceResponse(
        id=mp.id,
        name=mp.name,
        slug=mp.slug,
        description=mp.description,
        visibility=mp.visibility,
        signing_key_public=mp.signing_key_public,
        access_token=mp.access_token,
        created_at=mp.created_at,
    )


@router.get("/orgs/{org_slug}/marketplaces", response_model=list[MarketplaceResponse])
async def list_marketplaces(
    org_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = await _get_org_for_user(org_slug, user, db)
    result = await db.execute(
        select(Marketplace).where(Marketplace.org_id == org.id)
    )
    mps = result.scalars().all()
    responses = []
    for mp in mps:
        count_result = await db.execute(
            select(func.count(Plugin.id)).where(Plugin.marketplace_id == mp.id)
        )
        count = count_result.scalar() or 0
        responses.append(
            MarketplaceResponse(
                id=mp.id,
                name=mp.name,
                slug=mp.slug,
                description=mp.description,
                visibility=mp.visibility,
                signing_key_public=mp.signing_key_public,
                access_token=mp.access_token if mp.visibility == "private" else None,
                created_at=mp.created_at,
                plugin_count=count,
            )
        )
    return responses


@router.get("/me/marketplaces", response_model=list[MyMarketplaceResponse])
async def my_marketplaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Every marketplace the current user can access, across all their orgs,
    tagged with their relationship (created vs shared) and edit rights.

    Global editors additionally see the `official` catalog as a shared/admin
    marketplace so they can manage it from the dashboard.
    """

    async def _plugin_count(mp_id: str) -> int:
        r = await db.execute(select(func.count(Plugin.id)).where(Plugin.marketplace_id == mp_id))
        return r.scalar() or 0

    def _build(mp: Marketplace, org: Org, group: str, access: str, can_edit: bool, count: int):
        return MyMarketplaceResponse(
            id=mp.id,
            name=mp.name,
            slug=mp.slug,
            description=mp.description,
            visibility=mp.visibility,
            signing_key_public=mp.signing_key_public,
            access_token=mp.access_token if mp.visibility == "private" else None,
            created_at=mp.created_at,
            plugin_count=count,
            org_name=org.name if org else "",
            org_slug=org.slug if org else "",
            group=group,
            access=access,
            can_edit=can_edit,
        )

    items: list[MyMarketplaceResponse] = []
    seen: set[str] = set()

    rows = await db.execute(
        select(OrgMember, Org).join(Org, Org.id == OrgMember.org_id).where(OrgMember.user_id == user.id)
    )
    for member, org in rows.all():
        mps = await db.execute(select(Marketplace).where(Marketplace.org_id == org.id))
        for mp in mps.scalars():
            if mp.id in seen:
                continue
            seen.add(mp.id)
            can_edit = member.role in ("owner", "publisher")
            group = "created" if member.role == "owner" else "shared"
            items.append(_build(mp, org, group, member.role, can_edit, await _plugin_count(mp.id)))

    if is_global_editor(user):
        r = await db.execute(select(Marketplace).where(Marketplace.slug == "official"))
        official = r.scalar_one_or_none()
        if official and official.id not in seen:
            seen.add(official.id)
            org = await db.get(Org, official.org_id)
            items.append(_build(official, org, "shared", "admin", True, await _plugin_count(official.id)))

    return items


# ---------------------------------------------------------------------------
# Publish tokens — long-lived credentials for the publish/manage APIs
# ---------------------------------------------------------------------------


async def _marketplace_for_token_management(
    mp_slug: str, user: User, db: AsyncSession
) -> Marketplace:
    """The marketplace, if the caller may manage publish tokens for it.

    Same authorization as publishing (org owner/publisher or global editor),
    with one extra rule: a publish token cannot mint publish tokens — only a
    logged-in session can.
    """
    if getattr(user, "_publish_token_mp_id", None) is not None:
        raise HTTPException(403, "Publish tokens cannot manage publish tokens — log in instead")

    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    if is_global_editor(user):
        return mp

    membership = await db.execute(
        select(OrgMember).where(OrgMember.org_id == mp.org_id, OrgMember.user_id == user.id)
    )
    member = membership.scalar_one_or_none()
    if not member or member.role not in ("owner", "publisher"):
        raise HTTPException(403, "Must be owner or publisher to manage publish tokens")
    return mp


@router.get("/marketplaces/{mp_slug}/publish-token", response_model=PublishTokenInfo)
async def get_publish_token_info(
    mp_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mp = await _marketplace_for_token_management(mp_slug, user, db)
    result = await db.execute(
        select(PublishToken).where(
            PublishToken.user_id == user.id,
            PublishToken.marketplace_id == mp.id,
            PublishToken.revoked == False,  # noqa: E712
        )
    )
    pt = result.scalars().first()
    if not pt:
        return PublishTokenInfo(exists=False)
    return PublishTokenInfo(
        exists=True,
        token_prefix=pt.token_prefix,
        created_at=pt.created_at,
        last_used_at=pt.last_used_at,
    )


@router.post("/marketplaces/{mp_slug}/publish-token", response_model=PublishTokenCreated)
async def create_publish_token(
    mp_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create (or regenerate) the caller's publish token for this marketplace.

    Any previous token for the same (user, marketplace) is revoked. The full
    secret is returned only from this call.
    """
    mp = await _marketplace_for_token_management(mp_slug, user, db)

    existing = await db.execute(
        select(PublishToken).where(
            PublishToken.user_id == user.id,
            PublishToken.marketplace_id == mp.id,
            PublishToken.revoked == False,  # noqa: E712
        )
    )
    for old in existing.scalars():
        old.revoked = True

    secret = new_publish_token()
    pt = PublishToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        marketplace_id=mp.id,
        token_hash=hash_publish_token(secret),
        token_prefix=secret[:12],
        created_at=now_ts(),
    )
    db.add(pt)
    await db.commit()
    return PublishTokenCreated(token=secret, token_prefix=pt.token_prefix, created_at=pt.created_at)


@router.delete("/marketplaces/{mp_slug}/publish-token")
async def revoke_publish_token(
    mp_slug: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mp = await _marketplace_for_token_management(mp_slug, user, db)
    result = await db.execute(
        select(PublishToken).where(
            PublishToken.user_id == user.id,
            PublishToken.marketplace_id == mp.id,
            PublishToken.revoked == False,  # noqa: E712
        )
    )
    revoked = 0
    for pt in result.scalars():
        pt.revoked = True
        revoked += 1
    await db.commit()
    return {"status": "revoked", "count": revoked}


async def _get_org_for_user(
    org_slug: str, user: User, db: AsyncSession, min_role: str | None = None
) -> Org:
    result = await db.execute(select(Org).where(Org.slug == org_slug))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(404, "Org not found")

    membership = await db.execute(
        select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.user_id == user.id)
    )
    member = membership.scalar_one_or_none()
    if not member:
        raise HTTPException(403, "Not a member of this org")

    if min_role:
        role_hierarchy = ["viewer", "reviewer", "publisher", "owner"]
        if role_hierarchy.index(member.role) < role_hierarchy.index(min_role):
            raise HTTPException(403, f"Requires {min_role} role")

    return org
