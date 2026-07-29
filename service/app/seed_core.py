"""Seed core (repo-owned) plugins into the official marketplace on startup.

Core plugins live as source under `marketplace-src/` and are packaged
deterministically and upserted into the `official` marketplace every boot.
Idempotent: a version already present with the same sha256 is skipped; a
re-publish with different bytes for an existing version is refused (immutability).

Everything else (third-party plugins) is added at runtime via the upload API —
same DB tables, different ingestion path.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import storage, taxonomy
from .auth import hash_password
from .database import async_session
from .packaging import package_source
from .models.db import (
    Artifact,
    Marketplace,
    Org,
    OrgMember,
    Plugin,
    PluginMedia,
    PluginVersion,
    User,
    now_ts,
)

# Stable identifiers so Luna's pinned marketplace id never changes across deploys.
OFFICIAL_MP_ID = "00000000-0000-4000-8000-000000000001"
OFFICIAL_MP_SLUG = "official"
OFFICIAL_MP_NAME = "Luna-Marketplace"
OFFICIAL_ORG_ID = "00000000-0000-4000-8000-0000000000a1"
OFFICIAL_ORG_SLUG = "luna-official"
CORE_USER_ID = "00000000-0000-4000-8000-0000000000b1"
CORE_USER_EMAIL = "core@luna-marketplaces.local"

# Editorial curation for the official Discover page — kept in sync every boot,
# like the marketplace name. Slots referencing missing plugins fall back to
# download/rating order at read time (see routers/plugins.py discover()).
OFFICIAL_CURATION: dict = {
    "heroes": [
        {
            "plugin": "personality-template",
            "kicker": "Only on Luna",
            "title": "Give your agent a soul",
            "sub": "Iconic fictional personalities, one click away.",
        },
        {
            "plugin": "plugin-image-gen",
            "kicker": "Plugins we love",
            "title": "Create images mid-conversation",
            "sub": "Nano Banana Pro, GPT Image and FLUX, inline in chat.",
        },
    ],
    "essentials": ["plugin-web-access", "plugin-files", "plugin-playbooks"],
    "features": [
        {
            "plugin": "plugin-giphy",
            "kicker": "Plugins we love",
            "title": "The right GIF, right on time",
            "sub": "GIPHY search and inline reactions in the chat.",
        },
        {
            "plugin": "plugin-browser",
            "kicker": "New this month",
            "title": "A real browser for your agent",
            "sub": "Navigate, read pages and capture screenshots — stealth included.",
        },
    ],
}

# Content types for seeded media files.
_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
}


def _marketplace_src() -> Path:
    env = os.environ.get("MARKETPLACE_SRC")
    if env:
        return Path(env)
    # service/app/seed_core.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2] / "marketplace-src"


async def _ensure_official(db: AsyncSession) -> Marketplace:
    user = await db.get(User, CORE_USER_ID)
    if user is None:
        user = User(
            id=CORE_USER_ID,
            email=CORE_USER_EMAIL,
            username="luna-core",
            password_hash=hash_password(uuid.uuid4().hex),
            created_at=now_ts(),
        )
        db.add(user)

    org = await db.get(Org, OFFICIAL_ORG_ID)
    if org is None:
        org = Org(id=OFFICIAL_ORG_ID, name="Luna Official", slug=OFFICIAL_ORG_SLUG, created_at=now_ts())
        db.add(org)
        db.add(OrgMember(id=str(uuid.uuid4()), org_id=OFFICIAL_ORG_ID, user_id=CORE_USER_ID, role="owner"))

    mp = await db.get(Marketplace, OFFICIAL_MP_ID)
    if mp is None:
        mp = Marketplace(
            id=OFFICIAL_MP_ID,
            org_id=OFFICIAL_ORG_ID,
            name=OFFICIAL_MP_NAME,
            slug=OFFICIAL_MP_SLUG,
            description="First-party plugins maintained in the luna-marketplaces repo.",
            visibility="public",
            curation=OFFICIAL_CURATION,
            created_at=now_ts(),
        )
        db.add(mp)
    else:
        # Keep display name and curation in sync with the repo across deploys.
        if mp.name != OFFICIAL_MP_NAME:
            mp.name = OFFICIAL_MP_NAME
        if mp.curation != OFFICIAL_CURATION:
            mp.curation = OFFICIAL_CURATION

    await db.flush()
    return mp


def _tools_from_manifest(manifest: dict) -> list[dict]:
    return manifest.get("tools", []) or []


def _apply_permissions(plugin: Plugin, manifest: dict) -> None:
    """Mirror the toml's `[permissions]` onto the catalog row.

    Repo-seeded plugins used to skip this, so a plugin that reads the vault or
    talks to a host showed an empty Access panel — the pane would under-report
    what it does. `permissions.ui_iframe` etc. are declarative: the marketplace
    reports them, Luna asks the owner at install.
    """
    perms = manifest.get("permissions") or {}
    plugin.requires_ui_iframe = bool(perms.get("ui_iframe", False))
    plugin.requires_settings_tab = bool(perms.get("settings_tab", False))
    plugin.requires_vault_access = bool(perms.get("vault_access", False))
    plugin.requires_egress = list(perms.get("egress_hosts") or [])


async def _seed_media(db: AsyncSession, plugin: Plugin, pkg_dir: Path) -> None:
    """Sync `plugin_media` rows with `<pkg>/media/` files (not shipped in the
    artifact — packaging excludes the dir). Filename → kind: `icon.*`,
    `cover.*`, everything else a screenshot ordered by name. Idempotent:
    rows are replaced only when the file set changes."""
    media_dir = pkg_dir / "media"
    files = (
        sorted(p for p in media_dir.iterdir() if p.is_file() and p.suffix.lower() in _MEDIA_TYPES)
        if media_dir.is_dir()
        else []
    )

    desired: list[tuple[str, str, str, bytes]] = []  # (kind, sha256, content_type, bytes)
    order = 0
    for f in files:
        data = f.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        stem = f.stem.lower()
        kind = "icon" if stem == "icon" else "cover" if stem == "cover" else "screenshot"
        desired.append((kind, sha, _MEDIA_TYPES[f.suffix.lower()], data))
        order += 1

    existing = (
        await db.execute(select(PluginMedia).where(PluginMedia.plugin_id == plugin.id))
    ).scalars().all()
    if {(m.kind, m.sha256) for m in existing} == {(k, s) for k, s, _, _ in desired}:
        # Ensure bytes survive a recreated disk, then done.
        for _, sha, _, data in desired:
            if not storage.exists(sha, ext=".bin"):
                storage.store(sha, data, ext=".bin")
        return

    for m in existing:
        await db.delete(m)
    for i, (kind, sha, ctype, data) in enumerate(desired):
        storage.store(sha, data, ext=".bin")
        db.add(PluginMedia(
            id=str(uuid.uuid4()),
            plugin_id=plugin.id,
            kind=kind,
            sha256=sha,
            content_type=ctype,
            caption="",
            sort_order=i,
        ))


async def _upsert_plugin(db: AsyncSession, mp: Marketplace, manifest: dict, sha256: str, zip_bytes: bytes, pkg_dir: Path | None = None) -> str:
    name = manifest["name"]
    version = str(manifest["version"])
    tools = _tools_from_manifest(manifest)

    result = await db.execute(
        select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == name)
    )
    plugin = result.scalar_one_or_none()
    if plugin is None:
        plugin = Plugin(
            id=str(uuid.uuid4()),
            marketplace_id=mp.id,
            name=name,
            namespace=mp.slug,
            description=manifest.get("description", ""),
            readme=manifest.get("readme", ""),
            tags=manifest.get("tags", []),
            requires_tools=len(tools) > 0,
            tool_count=len(tools),
            tool_policies=tools,
            category=taxonomy.normalize(manifest.get("category")),
            created_at=now_ts(),
            updated_at=now_ts(),
        )
        _apply_permissions(plugin, manifest)
        db.add(plugin)
        await db.flush()
    else:
        plugin.description = manifest.get("description", plugin.description)
        plugin.readme = manifest.get("readme", plugin.readme)
        plugin.tags = manifest.get("tags", plugin.tags)
        plugin.tool_count = len(tools)
        plugin.tool_policies = tools
        plugin.requires_tools = len(tools) > 0
        cat = taxonomy.normalize(manifest.get("category"))
        if cat:
            plugin.category = cat
        _apply_permissions(plugin, manifest)
        plugin.updated_at = now_ts()

    if pkg_dir is not None:
        await _seed_media(db, plugin, pkg_dir)

    # Version immutability check.
    ver_result = await db.execute(
        select(PluginVersion).where(
            PluginVersion.plugin_id == plugin.id, PluginVersion.version == version
        )
    )
    existing = ver_result.scalar_one_or_none()
    if existing is not None:
        if existing.artifact_hash != sha256:
            return f"SKIP {name} {version}: already published with different bytes (immutable)"
        # Make sure the bytes are on disk (disk may have been recreated).
        if not storage.exists(sha256):
            storage.store(sha256, zip_bytes)
        return f"ok {name} {version} (unchanged)"

    storage.store(sha256, zip_bytes)
    if await db.get(Artifact, sha256) is None:
        db.add(Artifact(sha256=sha256, size=len(zip_bytes), created_at=now_ts()))

    manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    db.add(PluginVersion(
        id=str(uuid.uuid4()),
        plugin_id=plugin.id,
        version=version,
        artifact_hash=sha256,
        manifest_hash=manifest_hash,
        manifest_data=manifest,
        sdk_compat=str(manifest.get("sdk_version", "0")),
        capabilities_required=manifest.get("requires", {}),
        published_at=now_ts(),
    ))
    plugin.latest_version = version
    plugin.updated_at = now_ts()
    return f"seeded {name} {version} sha256={sha256[:12]}"


# Marks media rows owned by _seed_default_media so they can be re-synced when
# the repo art changes without ever touching publisher uploads.
_DEFAULT_MEDIA_CAPTION = "__default__"

# sha256 of default files seeded before the ownership marker existed; rows
# matching these are treated as owned. Removable once prod has re-seeded.
_LEGACY_DEFAULT_SHAS = {
    "1a680263e6e2b8ea610ad20e9453074aa6df8d2d3e5f5b276ea477f4284f4459",  # luna-macrunner/icon.svg
    "b86280c4fc06a9516896b5fb7603ca275a98d15898d68ecc836fc792cd788f93",  # personality-template/cover.png
    "7ea073f31706b4f6edcb2307bb158fbd279111cbbcf5895a7f1c47efa2319d2b",  # personality-template/icon.png
    "a9a6b8af2aaae247670a2a55bafb6e9bafc86c63d21ba8a31659ab781ca085bf",  # plugin-browser/cover.png
    "6e93fbb7e0d8b12f20d980495fa7fbd03a3fea572d6a2b2a340c7925db43d517",  # plugin-browser/icon.png
    "5eb3e563937f3754f75502ee3684a7c5db9afd1e8b3330b76c478bae87fb6724",  # plugin-curiosity/icon.svg
    "4ed38473deb560ddec25d1882e6ee972979d9683a850d31c2a075ec3d2a2a6af",  # plugin-feedback/icon.png
    "a41a547ec653587cb23fdf91fb0bc8748da33779b9de093023501cc64817bd8f",  # plugin-giphy/cover.png
    "b65764fb2dcec50125342b09766890d6ebeed7b4891e34e9357745466263ee0c",  # plugin-giphy/icon.png
    "da6abc3a2086d867911a6f45cdd7a0a750ff8c746c92ba9f1602e2c917efca2f",  # plugin-html-page/icon.png
    "c1552b72ecce50f123bbfcb78ad7ff815b91b08da12de2f30c2711d9163d3e33",  # plugin-image-gen/cover.png
    "976d30175560b91d3aa5079f9e0e4868f92852b4b5b38fe9a96535cf07d598ef",  # plugin-image-gen/icon.png
    "6f202444a3f3619b3625b158b9816b996176ea525efe4f644d3914397076a529",  # plugin-linear-ascent/icon.svg
    "c0988b39f3039cbdf0ebf9116bc0942f6a736fc0ac2119ee34c4efe2ba823061",  # plugin-scheduler/icon.svg
    "81eced722f5efbf1e3ded33cbc96214356cdb96bb947830510ceed0d6174ba85",  # plugin-socialkit/icon.png
    "bd9a76a7977aeb17ddc0bf7bced6792c6c6631bff61ca5e731ffb946ce2d1ac4",  # plugin-whatsapp/icon.png
    "0ee3f2ff7825e47f5ff3642b3a388cf06d4216b1fe2a1d413872188e75bbae80",  # plugin-wiki/icon.svg
}


async def _seed_default_media(db: AsyncSession, mp: Marketplace, src: Path) -> list[str]:
    """Default art for plugins NOT seeded from marketplace-src (published
    externally). `marketplace-src/_default_media/<plugin-name>/{icon,cover}.*`
    applies when the plugin has no media, and re-syncs rows this seeder owns
    (caption marker); anything a publisher uploaded is never touched."""
    root = src / "_default_media"
    if not root.is_dir():
        return []
    log: list[str] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        plugin = (
            await db.execute(
                select(Plugin).where(Plugin.marketplace_id == mp.id, Plugin.name == d.name)
            )
        ).scalar_one_or_none()
        if plugin is None:
            continue
        existing = (
            await db.execute(select(PluginMedia).where(PluginMedia.plugin_id == plugin.id))
        ).scalars().all()
        owned = all(
            m.caption == _DEFAULT_MEDIA_CAPTION or m.sha256 in _LEGACY_DEFAULT_SHAS
            for m in existing
        )
        if not owned:
            continue

        files = sorted(
            p for p in d.iterdir() if p.is_file() and p.suffix.lower() in _MEDIA_TYPES
        )
        desired: list[tuple[str, str, str, bytes]] = []  # (kind, sha, content_type, bytes)
        for f in files:
            data = f.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            stem = f.stem.lower()
            kind = "icon" if stem == "icon" else "cover" if stem == "cover" else "screenshot"
            desired.append((kind, sha, _MEDIA_TYPES[f.suffix.lower()], data))

        if {(m.kind, m.sha256) for m in existing} == {(k, s) for k, s, _, _ in desired}:
            for _, sha, _, data in desired:
                if not storage.exists(sha, ext=".bin"):
                    storage.store(sha, data, ext=".bin")
            continue

        for m in existing:
            await db.delete(m)
        for i, (kind, sha, ctype, data) in enumerate(desired):
            storage.store(sha, data, ext=".bin")
            db.add(PluginMedia(
                id=str(uuid.uuid4()),
                plugin_id=plugin.id,
                kind=kind,
                sha256=sha,
                content_type=ctype,
                caption=_DEFAULT_MEDIA_CAPTION,
                sort_order=i,
            ))
        if desired:
            log.append(f"default media for {d.name}: {len(desired)} file(s)")
    return log


async def seed_core_plugins() -> list[str]:
    """Package every plugin under marketplace-src/ and upsert into official."""
    src = _marketplace_src()
    log: list[str] = []
    if not src.exists():
        return [f"no marketplace-src at {src}"]

    async with async_session() as db:
        mp = await _ensure_official(db)
        for pkg in sorted(p for p in src.iterdir() if p.is_dir()):
            if not (pkg / "__init__.py").exists() or not (pkg / "luna-plugin.toml").exists():
                continue
            try:
                zip_bytes, sha256, manifest = package_source(pkg)
                log.append(await _upsert_plugin(db, mp, manifest, sha256, zip_bytes, pkg_dir=pkg))
            except Exception as e:  # noqa: BLE001
                log.append(f"ERROR {pkg.name}: {e}")
        log.extend(await _seed_default_media(db, mp, src))
        await db.commit()
    return log
