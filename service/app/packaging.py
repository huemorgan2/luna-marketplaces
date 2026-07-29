"""Deterministic plugin packaging + manifest parsing.

A plugin's distributable artifact is a zip with exactly ONE top-level directory
(the Python package, e.g. `hello_world/`) containing `__init__.py` and a
`luna-plugin.toml` data manifest. Luna's installer (`luna/luna/plugins/install.py`)
enforces the single-top-level-dir rule and verifies the artifact's sha256 against
the marketplace index before loading.

Packaging MUST be deterministic so the sha256 is stable across rebuilds:
sorted entries, fixed timestamps, no `__pycache__`. The same packager is used by
the core-plugin seeder (from `marketplace-src/`) and re-used to hash uploaded
zips, so a given source always yields the same hash.
"""

from __future__ import annotations

import hashlib
import io
import tomllib
import zipfile
from pathlib import Path
from typing import Any

# Fixed DOS timestamp (1980-01-01 00:00:00) for reproducible zips.
_FIXED_DATE = (1980, 1, 1, 0, 0, 0)

MANIFEST_NAME = "luna-plugin.toml"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_manifest_toml(raw: bytes) -> dict[str, Any]:
    """Parse a luna-plugin.toml into a manifest dict."""
    return tomllib.loads(raw.decode("utf-8"))


def read_manifest_from_dir(package_dir: Path) -> dict[str, Any]:
    """Read the manifest from a package dir's luna-plugin.toml."""
    toml_path = package_dir / MANIFEST_NAME
    if not toml_path.exists():
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {package_dir}")
    return parse_manifest_toml(toml_path.read_bytes())


def package_dir_to_zip(package_dir: Path) -> bytes:
    """Deterministically zip a package directory, preserving its top-level name.

    Result contains `<package_dir.name>/...` as the single top-level dir.
    """
    files = sorted(
        p for p in package_dir.rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix != ".pyc"
        # `media/` holds marketing images seeded into plugin_media — never
        # shipped inside the artifact (would bloat installs + churn the sha256).
        and (len(p.relative_to(package_dir).parts) < 2 or p.relative_to(package_dir).parts[0] != "media")
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            arcname = str(p.relative_to(package_dir.parent))
            info = zipfile.ZipInfo(arcname, date_time=_FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())
    return buf.getvalue()


def package_source(package_dir: Path) -> tuple[bytes, str, dict[str, Any]]:
    """Package a source package dir → (zip_bytes, sha256, manifest)."""
    manifest = read_manifest_from_dir(package_dir)
    zip_bytes = package_dir_to_zip(package_dir)
    return zip_bytes, sha256_hex(zip_bytes), manifest


def read_manifest_from_zip(zip_bytes: bytes) -> tuple[dict[str, Any], str]:
    """Extract the manifest from an uploaded artifact zip.

    Returns (manifest, top_level_dir). Validates the single-top-level-dir rule
    and that the package has an __init__.py — the same invariants Luna enforces.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n and not n.endswith("/")]
        if not names:
            raise ValueError("artifact zip is empty")
        tops = {n.split("/", 1)[0] for n in names}
        if len(tops) != 1:
            raise ValueError(f"artifact must contain exactly one top-level dir, got: {sorted(tops)}")
        top = tops.pop()
        if f"{top}/__init__.py" not in names:
            raise ValueError(f"package '{top}' is missing __init__.py")
        manifest_path = f"{top}/{MANIFEST_NAME}"
        if manifest_path not in names:
            raise ValueError(f"package '{top}' is missing {MANIFEST_NAME}")
        manifest = parse_manifest_toml(zf.read(manifest_path))
    return manifest, top


def index_entry_from_manifest(manifest: dict[str, Any], sha256: str) -> dict[str, Any]:
    """Build the Luna-v0 index.json plugin entry from a manifest + hash."""
    name = manifest["name"]
    version = manifest["version"]
    return {
        "name": name,
        "version": version,
        "description": manifest.get("description", ""),
        "sdk_version": str(manifest.get("sdk_version", "0")),
        "requires": manifest.get("requires", {}),
        "artifact": f"plugins/{name}/{version}/artifact.zip",
        "sha256": sha256,
    }
