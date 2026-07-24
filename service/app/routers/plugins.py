"""Plugin publishing and catalog API routes."""

from __future__ import annotations

import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from .. import storage
from ..auth import get_current_user, is_global_editor
from ..database import get_db
from ..models.db import Artifact, Marketplace, Org, OrgMember, Plugin, PluginVersion, User, UsageEvent, now_ts
from ..packaging import read_manifest_from_zip
from ..models.schemas import PluginResponse, PluginUpdate, PluginVersionResponse, YankRequest

router = APIRouter()


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
        )
        db.add(plugin)

    plugin.latest_version = version
    plugin.description = manifest_data.get("description", plugin.description)
    plugin.readme = manifest_data.get("readme", plugin.readme)
    plugin.tags = manifest_data.get("tags", plugin.tags)
    plugin.tool_count = len(tools)
    plugin.tool_policies = tools
    plugin.requires_tools = len(tools) > 0
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
    license_filter: str | None = Query(None, alias="license"),
    requires_ui: bool | None = Query(None),
    requires_vault: bool | None = Query(None),
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
    if license_filter:
        query = query.where(Plugin.license == license_filter)
    if requires_ui is not None:
        query = query.where(Plugin.requires_ui_iframe == requires_ui)
    if requires_vault is not None:
        query = query.where(Plugin.requires_vault_access == requires_vault)

    plugins_result = await db.execute(query)
    plugins = plugins_result.scalars().all()

    # Filter by tags in Python (JSON column)
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        plugins = [p for p in plugins if any(t in (p.tags or []) for t in tag_list)]

    return [
        PluginResponse(
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
        )
        for p in plugins
    ]


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
    )


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
    for key, value in fields.items():
        setattr(p, key, value)
    if fields:
        p.updated_at = now_ts()
    await db.commit()
    await db.refresh(p)

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
    )


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
    await db.delete(p)
    await db.flush()

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
