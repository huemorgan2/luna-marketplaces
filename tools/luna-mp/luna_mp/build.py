"""Build a static marketplace from a folder of plugin directories."""

from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path

from .schemas import (
    IndexEntry,
    MarketplaceIdentity,
    MarketplaceIndex,
    PluginCompat,
    PluginPermissions,
    PluginProvenance,
    PluginVersions,
    PublishedManifest,
    Snapshot,
    Timestamp,
    ToolPermission,
    VersionEntry,
)
from .signing import KeyPair, canonicalize, hash_bytes, hash_file, sign_payload


def load_plugin_manifest(plugin_dir: Path) -> PublishedManifest:
    """Load and validate a plugin manifest from a directory.

    Looks for manifest.json in the plugin directory.
    """
    manifest_path = plugin_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest.json in {plugin_dir}")

    data = json.loads(manifest_path.read_text())
    return PublishedManifest(**data)


def package_plugin(plugin_dir: Path, output_dir: Path) -> tuple[Path, str]:
    """Zip a plugin folder into an artifact. Returns (zip_path, sha256_hash)."""
    name = plugin_dir.name
    zip_path = output_dir / f"{name}.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(plugin_dir.rglob("*")):
            if file.is_file():
                arcname = file.relative_to(plugin_dir)
                zf.write(file, arcname)

    artifact_hash = hash_file(zip_path)
    return zip_path, artifact_hash


def build_marketplace(
    source_dir: Path,
    output_dir: Path,
    key: KeyPair,
    marketplace_id: str,
    marketplace_name: str,
    publisher_key: KeyPair | None = None,
) -> Path:
    """Build a complete static marketplace from a source directory of plugins.

    source_dir: folder containing plugin subdirectories (each with manifest.json)
    output_dir: where to write the static marketplace files
    key: marketplace signing key
    marketplace_id: UUID for the marketplace
    marketplace_name: human-readable name

    Returns the output directory.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    plugins_dir = output_dir / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    wellknown_dir = output_dir / ".well-known"
    wellknown_dir.mkdir(exist_ok=True)

    now = int(time.time())
    index_entries: list[IndexEntry] = []
    snapshot_files: dict[str, str] = {}

    plugin_dirs = sorted(
        [d for d in source_dir.iterdir() if d.is_dir() and (d / "manifest.json").exists()]
    )

    for plugin_dir in plugin_dirs:
        manifest = load_plugin_manifest(plugin_dir)
        plugin_out = plugins_dir / manifest.name
        plugin_out.mkdir(parents=True, exist_ok=True)
        version_out = plugin_out / manifest.version
        version_out.mkdir(parents=True, exist_ok=True)

        # Package artifact
        zip_path, artifact_hash = package_plugin(plugin_dir, version_out)
        final_artifact = version_out / "artifact.zip"
        if zip_path != final_artifact:
            shutil.move(str(zip_path), str(final_artifact))

        # Write manifest
        manifest_json = manifest.model_dump(mode="json", exclude_none=True)
        manifest_bytes = json.dumps(manifest_json, indent=2).encode()
        manifest_hash = hash_bytes(manifest_bytes)
        (version_out / "manifest.json").write_bytes(manifest_bytes)

        # Sign manifest with publisher key if available
        signer = publisher_key or key
        manifest_envelope = sign_payload(manifest_json, signer.signing_key)
        if publisher_key and publisher_key != key:
            # Countersign with marketplace key
            existing_sig = manifest_envelope["signatures"][0]
            mp_sig_bytes = key.signing_key.sign(
                canonicalize(manifest_json),
            )
            from nacl.encoding import HexEncoder as HE

            manifest_envelope["signatures"].append(
                {
                    "keyid": key.public_hex,
                    "sig": key.signing_key.sign(
                        canonicalize(manifest_json), encoder=HE
                    ).signature.decode(),
                }
            )
        (version_out / "manifest.signed.json").write_text(
            json.dumps(manifest_envelope, indent=2)
        )

        # Build/update versions file
        versions_path = plugin_out / "versions.json"
        if versions_path.exists():
            existing = json.loads(versions_path.read_text())
            existing_payload = existing.get("payload", existing)
            plugin_versions = PluginVersions(**existing_payload)
            # Check immutability
            for v in plugin_versions.versions:
                if v.version == manifest.version:
                    if v.artifact_hash != artifact_hash:
                        raise ValueError(
                            f"Cannot overwrite existing version {manifest.version} of "
                            f"{manifest.name} with different content (immutability rule)"
                        )
                    break
            else:
                plugin_versions.versions.append(
                    VersionEntry(
                        version=manifest.version,
                        artifact_hash=artifact_hash,
                        manifest_hash=manifest_hash,
                        published_at=now,
                        compat=manifest.compat,
                    )
                )
                plugin_versions.latest = manifest.version
        else:
            plugin_versions = PluginVersions(
                name=manifest.name,
                namespace=manifest.namespace,
                versions=[
                    VersionEntry(
                        version=manifest.version,
                        artifact_hash=artifact_hash,
                        manifest_hash=manifest_hash,
                        published_at=now,
                        compat=manifest.compat,
                    )
                ],
                latest=manifest.version,
            )

        # Sign and write versions file
        versions_payload = plugin_versions.model_dump(mode="json")
        versions_envelope = sign_payload(versions_payload, key.signing_key)
        versions_path.write_text(json.dumps(versions_envelope, indent=2))
        snapshot_files[f"plugins/{manifest.name}/versions.json"] = hash_bytes(
            canonicalize(versions_payload)
        )

        # Index entry
        index_entries.append(
            IndexEntry(
                name=manifest.name,
                namespace=manifest.namespace,
                latest_version=manifest.version,
                description=manifest.description,
                tags=manifest.tags,
            )
        )

    # Build index
    index = MarketplaceIndex(
        marketplace_id=marketplace_id,
        generated_at=now,
        plugin_count=len(index_entries),
        plugins=index_entries,
    )
    index_payload = index.model_dump(mode="json")
    index_envelope = sign_payload(index_payload, key.signing_key)
    (output_dir / "index.json").write_text(json.dumps(index_envelope, indent=2))
    snapshot_files["index.json"] = hash_bytes(canonicalize(index_payload))

    # Build snapshot
    snapshot = Snapshot(signed_at=now, version=1, files=snapshot_files)
    snapshot_payload = snapshot.model_dump(mode="json")
    snapshot_envelope = sign_payload(snapshot_payload, key.signing_key)
    (output_dir / "snapshot.json").write_text(json.dumps(snapshot_envelope, indent=2))

    # Build timestamp
    timestamp = Timestamp(signed_at=now, expires_at=now + 7 * 86400, version=1)
    timestamp_payload = timestamp.model_dump(mode="json")
    timestamp_envelope = sign_payload(timestamp_payload, key.signing_key)
    (output_dir / "timestamp.json").write_text(json.dumps(timestamp_envelope, indent=2))

    # Identity document
    identity = MarketplaceIdentity(
        id=marketplace_id,
        name=marketplace_name,
        signing_keys=[key.public_hex],
    )
    identity_doc = identity.model_dump(mode="json", exclude_none=True)
    (wellknown_dir / "luna-marketplace.json").write_text(
        json.dumps(identity_doc, indent=2)
    )

    return output_dir
