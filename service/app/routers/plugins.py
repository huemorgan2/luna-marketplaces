"""Plugin publishing and catalog API routes."""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .. import storage, taxonomy
from ..auth import get_current_user, is_global_editor
from ..database import get_db
from ..models.db import (
    Artifact,
    Marketplace,
    Org,
    OrgMember,
    Plugin,
    PluginMedia,
    PluginVersion,
    Review,
    ReviewVote,
    User,
    UsageEvent,
    now_ts,
)
from ..packaging import read_manifest_from_zip
from ..models.schemas import MediaItem, PluginResponse, PluginUpdate, PluginVersionResponse, YankRequest

router = APIRouter()


def plugin_response(p: Plugin, mp: Marketplace, media: list[PluginMedia] | None = None) -> PluginResponse:
    """Build the catalog response for a plugin (single construction point)."""
    return PluginResponse(
        id=p.id,
        name=p.name,
        namespace=p.namespace,
        description=p.description,
        readme=p.readme or "",
        tags=p.tags or [],
        license=p.license,
        icon_url=p.icon_url,
        source_url=p.source_url,
        latest_version=p.latest_version,
        download_count=p.download_count,
        created_at=p.created_at,
        updated_at=p.updated_at,
        requires_tools=p.requires_tools,
        requires_ui_iframe=p.requires_ui_iframe,
        requires_settings_tab=p.requires_settings_tab,
        requires_vault_access=p.requires_vault_access,
        requires_egress=p.requires_egress or [],
        tool_count=p.tool_count,
        tool_policies=p.tool_policies or [],
        marketplace_slug=mp.slug,
        marketplace_name=mp.name,
        category=p.category,
        rating_average=p.rating_average or 0.0,
        rating_count=p.rating_count or 0,
        media=[
            MediaItem(
                kind=m.kind,
                sha256=m.sha256,
                content_type=m.content_type,
                caption=m.caption or "",
                url=f"/media/{m.sha256}",
            )
            for m in (media or [])
        ],
    )


async def media_for_plugins(db: AsyncSession, plugin_ids: list[str]) -> dict[str, list[PluginMedia]]:
    """Fetch media rows for a set of plugins, ordered for display."""
    if not plugin_ids:
        return {}
    rows = await db.execute(
        select(PluginMedia)
        .where(PluginMedia.plugin_id.in_(plugin_ids))
        .order_by(PluginMedia.sort_order, PluginMedia.created_at)
    )
    out: dict[str, list[PluginMedia]] = {}
    for m in rows.scalars():
        out.setdefault(m.plugin_id, []).append(m)
    return out


@router.post("/marketplaces/{mp_slug}/upload")
async def upload_plugin(
    mp_slug: str,
    artifact: UploadFile = File(...),
    readme: str | None = Form(None),
    tags: str | None = Form(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a plugin by uploading just its artifact zip.

    The manifest (`luna-plugin.toml`) is read from INSIDE the zip — single
    source of truth, matching how a developer authors the plugin. Optional
    `readme`/`tags` form fields override the manifest values.
    """
    mp = await _get_marketplace_for_publisher(mp_slug, user, db)

    artifact_bytes = await artifact.read()
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    try:
        manifest_data, _top = read_manifest_from_zip(artifact_bytes)
    except ValueError as e:
        raise HTTPException(400, f"invalid plugin artifact: {e}")

    if readme is not None:
        manifest_data["readme"] = readme
    if tags is not None:
        manifest_data["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    return await _ingest_version(db, mp, manifest_data, artifact_bytes, artifact_hash)


@router.post("/marketplaces/{mp_slug}/publish")
async def publish_plugin(
    mp_slug: str,
    manifest: str = Form(...),
    artifact: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Publish a plugin version with an explicit manifest JSON (legacy/API path)."""
    mp = await _get_marketplace_for_publisher(mp_slug, user, db)
    manifest_data = json.loads(manifest)
    artifact_bytes = await artifact.read()
    artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
    return await _ingest_version(db, mp, manifest_data, artifact_bytes, artifact_hash)


async def _ingest_version(
    db: AsyncSession,
    mp: Marketplace,
    manifest_data: dict,
    artifact_bytes: bytes,
    artifact_hash: str,
):
    """Shared publish path: validate, persist artifact to disk, upsert rows."""
    name = manifest_data.get("name")
    namespace = manifest_data.get("namespace", mp.slug)
    version = manifest_data.get("version")
    if not name or not version:
        raise HTTPException(400, "Manifest must include name and version")
    version = str(version)

    manifest_hash = hashlib.sha256(json.dumps(manifest_data, sort_keys=True).encode()).hexdigest()

    result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == name)
    )
    plugin = result.scalar_one_or_none()

    if plugin:
        ver_result = await db.execute(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin.id,
                PluginVersion.version == version,
            )
        )
        existing_ver = ver_result.scalar_one_or_none()
        if existing_ver:
            if existing_ver.artifact_hash != artifact_hash:
                raise HTTPException(
                    409, f"Version {version} already exists with different content (immutability rule)"
                )
            raise HTTPException(409, f"Version {version} already published")

    # Tools/permissions come either from a flat `tools` list (toml manifest) or
    # a `permissions.tools` block (richer JSON manifest).
    permissions = manifest_data.get("permissions", {})
    tools = manifest_data.get("tools") or permissions.get("tools", []) or []

    raw_category = manifest_data.get("category")
    category = taxonomy.normalize(raw_category)
    if raw_category and category is None:
        raise HTTPException(
            400,
            f"Unknown category '{raw_category}'. Valid: {', '.join(sorted(taxonomy.CATEGORIES))}",
        )

    if plugin is None:
        plugin = Plugin(
            id=str(uuid.uuid4()),
            marketplace_id=mp.id,
            name=name,
            namespace=namespace,
            description=manifest_data.get("description", ""),
            readme=manifest_data.get("readme", ""),
            tags=manifest_data.get("tags", []),
            license=manifest_data.get("license", "MIT"),
            icon_url=manifest_data.get("icon"),
            source_url=manifest_data.get("provenance", {}).get("source") if isinstance(manifest_data.get("provenance"), dict) else None,
            requires_tools=len(tools) > 0,
            requires_ui_iframe=permissions.get("ui_iframe", False),
            requires_settings_tab=permissions.get("settings_tab", False),
            requires_vault_access=permissions.get("vault_access", False),
            requires_egress=permissions.get("egress_hosts", []),
            tool_count=len(tools),
            tool_policies=tools,
            category=category,
        )
        db.add(plugin)

    plugin.latest_version = version
    plugin.description = manifest_data.get("description", plugin.description)
    plugin.readme = manifest_data.get("readme", plugin.readme)
    plugin.tags = manifest_data.get("tags", plugin.tags)
    plugin.tool_count = len(tools)
    plugin.tool_policies = tools
    plugin.requires_tools = len(tools) > 0
    if category:
        plugin.category = category
    plugin.updated_at = now_ts()

    compat = manifest_data.get("compat", {})
    requires = compat.get("requires") or manifest_data.get("requires", {})
    sdk_compat = compat.get("sdk") or str(manifest_data.get("sdk_version", "0"))
    pv = PluginVersion(
        id=str(uuid.uuid4()),
        plugin_id=plugin.id,
        version=version,
        artifact_hash=artifact_hash,
        manifest_hash=manifest_hash,
        manifest_data=manifest_data,
        sdk_compat=sdk_compat,
        capabilities_required=requires,
    )
    db.add(pv)

    # Persist artifact bytes to the durable disk (content-addressed).
    storage.store(artifact_hash, artifact_bytes)
    if await db.get(Artifact, artifact_hash) is None:
        db.add(Artifact(sha256=artifact_hash, size=len(artifact_bytes), created_at=now_ts()))

    db.add(UsageEvent(
        id=str(uuid.uuid4()),
        org_id=mp.org_id,
        marketplace_id=mp.id,
        event_type="publish",
        plugin_name=f"{namespace}/{name}",
        metadata_={"version": version},
    ))

    await db.commit()
    return {"status": "published", "plugin": f"{namespace}/{name}", "version": version}


@router.get("/catalog/{mp_slug}", response_model=list[PluginResponse])
async def catalog(
    mp_slug: str,
    search: str | None = Query(None),
    tags: str | None = Query(None),
    category: str | None = Query(None),
    license_filter: str | None = Query(None, alias="license"),
    requires_ui: bool | None = Query(None),
    requires_vault: bool | None = Query(None),
    sort: str = Query("name"),  # name | rating | downloads | updated
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Browse the plugin catalog for a marketplace."""
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    query = select(Plugin).where(Plugin.marketplace_id == mp.id)

    if search:
        query = query.where(
            or_(
                Plugin.name.contains(search),
                Plugin.description.contains(search),
            )
        )
    if category:
        query = query.where(Plugin.category == category)
    if license_filter:
        query = query.where(Plugin.license == license_filter)
    if requires_ui is not None:
        query = query.where(Plugin.requires_ui_iframe == requires_ui)
    if requires_vault is not None:
        query = query.where(Plugin.requires_vault_access == requires_vault)

    if sort == "rating":
        query = query.order_by(Plugin.rating_average.desc(), Plugin.rating_count.desc())
    elif sort == "downloads":
        query = query.order_by(Plugin.download_count.desc())
    elif sort == "updated":
        query = query.order_by(Plugin.updated_at.desc())
    else:
        query = query.order_by(Plugin.name)

    plugins_result = await db.execute(query)
    plugins = plugins_result.scalars().all()

    # Filter by tags in Python (JSON column)
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        plugins = [p for p in plugins if any(t in (p.tags or []) for t in tag_list)]

    plugins = plugins[(page - 1) * per_page : page * per_page]

    media_map = await media_for_plugins(db, [p.id for p in plugins])
    return [plugin_response(p, mp, media_map.get(p.id)) for p in plugins]


@router.get("/catalog/{mp_slug}/discover")
async def discover(
    mp_slug: str,
    db: AsyncSession = Depends(get_db),
):
    """Editorial Discover feed: heroes, essentials, features, top picks, and
    per-category sections. Curated slots come from marketplaces.curation
    (JSON); anything uncurated falls back to download/rating order.
    """
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    all_plugins = (
        await db.execute(select(Plugin).where(Plugin.marketplace_id == mp.id))
    ).scalars().all()
    by_name = {p.name: p for p in all_plugins}
    media_map = await media_for_plugins(db, [p.id for p in all_plugins])

    def card(p: Plugin) -> dict:
        return plugin_response(p, mp, media_map.get(p.id)).model_dump()

    curation = mp.curation or {}
    used: set[str] = set()

    def take(names: list[str], count: int, ranked: list[Plugin]) -> list[Plugin]:
        """Curated names first (in order), topped up from `ranked`, no repeats."""
        out: list[Plugin] = []
        for n in names:
            p = by_name.get(n)
            if p and p.name not in used:
                out.append(p)
                used.add(p.name)
            if len(out) >= count:
                return out
        for p in ranked:
            if len(out) >= count:
                break
            if p.name not in used:
                out.append(p)
                used.add(p.name)
        return out

    by_downloads = sorted(all_plugins, key=lambda p: p.download_count or 0, reverse=True)
    by_rating = sorted(
        all_plugins, key=lambda p: ((p.rating_average or 0), (p.rating_count or 0)), reverse=True
    )

    hero_spec = curation.get("heroes", [])
    heroes = take([h.get("plugin", "") for h in hero_spec], 2, by_downloads)
    hero_meta = {h.get("plugin"): h for h in hero_spec}
    essentials = take(curation.get("essentials", []), 3, by_downloads)
    feature_spec = curation.get("features", [])
    features = take([f.get("plugin", "") for f in feature_spec], 2, by_downloads)
    feature_meta = {f.get("plugin"): f for f in feature_spec}
    top_picks = take([], 3, by_rating)

    def with_meta(p: Plugin, meta: dict) -> dict:
        c = card(p)
        # Empty fallbacks on purpose: the client derives a display name from
        # p.name and a kicker from the category when curation has no copy.
        c["kicker"] = meta.get("kicker", "")
        c["hero_title"] = meta.get("title", "")
        c["hero_sub"] = meta.get("sub", p.description or "")
        return c

    categories: list[dict] = []
    for slug, label in taxonomy.CATEGORIES.items():
        members = [p for p in all_plugins if p.category == slug]
        if not members:
            continue
        members.sort(key=lambda p: p.download_count or 0, reverse=True)
        categories.append({
            "slug": slug,
            "label": label,
            "total": len(members),
            "plugins": [card(p) for p in members[:3]],
        })

    return {
        "marketplace": {"name": mp.name, "slug": mp.slug, "description": mp.description or ""},
        "heroes": [with_meta(p, hero_meta.get(p.name, {})) for p in heroes],
        "essentials": [card(p) for p in essentials],
        "features": [with_meta(p, feature_meta.get(p.name, {})) for p in features],
        "top_picks": [card(p) for p in top_picks],
        "categories": categories,
    }


@router.get("/catalog/{mp_slug}/{plugin_name}", response_model=PluginResponse)
async def get_plugin(
    mp_slug: str,
    plugin_name: str,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed plugin info."""
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    plugin_result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == plugin_name)
    )
    p = plugin_result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Plugin not found")

    media_map = await media_for_plugins(db, [p.id])
    return plugin_response(p, mp, media_map.get(p.id))


@router.get("/catalog/{mp_slug}/{plugin_name}/versions", response_model=list[PluginVersionResponse])
async def get_plugin_versions(
    mp_slug: str,
    plugin_name: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    plugin_result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == plugin_name)
    )
    p = plugin_result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Plugin not found")

    versions_result = await db.execute(
        select(PluginVersion).where(PluginVersion.plugin_id == p.id).order_by(PluginVersion.published_at.desc())
    )
    versions = versions_result.scalars().all()

    return [
        PluginVersionResponse(
            id=v.id,
            version=v.version,
            artifact_hash=v.artifact_hash,
            sdk_compat=v.sdk_compat,
            capabilities_required=v.capabilities_required or {},
            published_at=v.published_at,
            yanked=v.yanked,
        )
        for v in versions
    ]


async def _get_plugin_for_editor(
    mp_slug: str, plugin_name: str, user: User, db: AsyncSession
) -> tuple[Marketplace, Plugin]:
    """Resolve a plugin and assert the user may edit its marketplace."""
    mp = await _get_marketplace_for_publisher(mp_slug, user, db)
    result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == plugin_name)
    )
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(404, "Plugin not found")
    return mp, plugin


@router.patch("/marketplaces/{mp_slug}/plugins/{plugin_name}", response_model=PluginResponse)
async def update_plugin(
    mp_slug: str,
    plugin_name: str,
    data: PluginUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit a plugin's catalog metadata (description, tags, license, links)."""
    mp, p = await _get_plugin_for_editor(mp_slug, plugin_name, user, db)

    fields = data.model_dump(exclude_unset=True)
    if "category" in fields and fields["category"] is not None:
        normalized = taxonomy.normalize(fields["category"])
        if normalized is None:
            raise HTTPException(
                400,
                f"Unknown category '{fields['category']}'. Valid: {', '.join(sorted(taxonomy.CATEGORIES))}",
            )
        fields["category"] = normalized
    for key, value in fields.items():
        setattr(p, key, value)
    if fields:
        p.updated_at = now_ts()
    await db.commit()
    await db.refresh(p)

    media_map = await media_for_plugins(db, [p.id])
    return plugin_response(p, mp, media_map.get(p.id))


@router.delete("/marketplaces/{mp_slug}/plugins/{plugin_name}")
async def delete_plugin(
    mp_slug: str,
    plugin_name: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a plugin: all versions, its usage-event history, and
    its artifact bytes.

    Artifacts are content-addressed and may back other plugins' versions (any
    marketplace), so bytes are only removed once no PluginVersion references
    the hash.
    """
    mp, p = await _get_plugin_for_editor(mp_slug, plugin_name, user, db)

    versions = await db.execute(select(PluginVersion).where(PluginVersion.plugin_id == p.id))
    hashes: set[str] = set()
    versions_removed = 0
    for v in versions.scalars():
        hashes.add(v.artifact_hash)
        await db.delete(v)
        versions_removed += 1

    # Reviews, votes, and media go with the plugin.
    reviews = await db.execute(select(Review).where(Review.plugin_id == p.id))
    review_ids = [r.id for r in reviews.scalars()]
    if review_ids:
        votes = await db.execute(select(ReviewVote).where(ReviewVote.review_id.in_(review_ids)))
        for vote in votes.scalars():
            await db.delete(vote)
        reviews = await db.execute(select(Review).where(Review.plugin_id == p.id))
        for r in reviews.scalars():
            await db.delete(r)
    media_rows = await db.execute(select(PluginMedia).where(PluginMedia.plugin_id == p.id))
    media_hashes: set[str] = set()
    for m in media_rows.scalars():
        media_hashes.add(m.sha256)
        await db.delete(m)

    await db.delete(p)
    await db.flush()

    # Media bytes are content-addressed too; only unlink hashes no other plugin uses.
    orphaned_media: list[str] = []
    for h in media_hashes:
        still = await db.execute(select(PluginMedia.id).where(PluginMedia.sha256 == h).limit(1))
        if still.scalar_one_or_none() is None:
            orphaned_media.append(h)

    orphaned: list[str] = []
    for h in hashes:
        still_used = await db.execute(
            select(PluginVersion.id).where(PluginVersion.artifact_hash == h).limit(1)
        )
        if still_used.scalar_one_or_none() is not None:
            continue
        artifact_row = await db.get(Artifact, h)
        if artifact_row is not None:
            await db.delete(artifact_row)
        orphaned.append(h)

    events = await db.execute(
        select(UsageEvent).where(
            UsageEvent.marketplace_id == mp.id,
            UsageEvent.plugin_name == f"{p.namespace}/{p.name}",
        )
    )
    events_purged = 0
    for e in events.scalars():
        await db.delete(e)
        events_purged += 1

    await db.commit()

    # Unlink bytes only after the rows are durably gone; a failed commit must
    # not leave live versions pointing at missing files.
    for h in orphaned:
        storage.delete(h)
    for h in orphaned_media:
        storage.delete(h, ext=".bin")

    return {
        "status": "deleted",
        "plugin": f"{mp.slug}/{plugin_name}",
        "versions_removed": versions_removed,
        "artifacts_purged": len(orphaned),
        "events_purged": events_purged,
    }


@router.post("/marketplaces/{mp_slug}/plugins/{plugin_name}/versions/{version}/yank")
async def yank_version(
    mp_slug: str,
    plugin_name: str,
    version: str,
    data: YankRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Yank (hide) or un-yank a specific plugin version."""
    _mp, p = await _get_plugin_for_editor(mp_slug, plugin_name, user, db)

    result = await db.execute(
        select(PluginVersion).where(
            PluginVersion.plugin_id == p.id, PluginVersion.version == version
        )
    )
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(404, "Version not found")
    v.yanked = data.yanked
    await db.commit()
    return {"status": "yanked" if data.yanked else "unyanked", "version": version}


async def _get_marketplace_for_publisher(mp_slug: str, user: User, db: AsyncSession) -> Marketplace:
    result = await db.execute(select(Marketplace).where(Marketplace.slug == mp_slug))
    mp = result.scalar_one_or_none()
    if not mp:
        raise HTTPException(404, "Marketplace not found")

    # Requests authenticated with a publish token are scoped to exactly the
    # marketplace the token was issued for, regardless of the user's roles.
    token_mp_id = getattr(user, "_publish_token_mp_id", None)
    if token_mp_id is not None and token_mp_id != mp.id:
        raise HTTPException(403, "Publish token is not valid for this marketplace")

    # Global editors (allow list) may publish to any catalog, including `official`
    # which has no real account behind it.
    if is_global_editor(user):
        return mp

    # Otherwise: must be a user of the catalog's account (org) with edit rights.
    membership = await db.execute(
        select(OrgMember).where(OrgMember.org_id == mp.org_id, OrgMember.user_id == user.id)
    )
    member = membership.scalar_one_or_none()
    if not member or member.role not in ("owner", "publisher"):
        raise HTTPException(403, "Must be owner or publisher to publish plugins")

    return mp
