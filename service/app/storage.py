"""Content-addressed artifact storage on a persistent disk.

Artifacts are stored by sha256 at `{ARTIFACTS_DIR}/{sha256[:2]}/{sha256}.zip`.
On Render this directory lives on a **mounted persistent disk** (so uploads
survive deploys/restarts); locally it falls back to `service/data/artifacts`.

Content-addressing gives us free dedupe and makes the stored bytes match the
hash Luna verifies against the index.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT = Path(__file__).parent.parent / "data" / "artifacts"
ARTIFACTS_DIR = Path(os.environ.get("ARTIFACTS_DIR", str(_DEFAULT)))


def _path_for(sha256: str, ext: str = ".zip") -> Path:
    return ARTIFACTS_DIR / sha256[:2] / f"{sha256}{ext}"


def exists(sha256: str, ext: str = ".zip") -> bool:
    return _path_for(sha256, ext).exists()


def store(sha256: str, data: bytes, ext: str = ".zip") -> Path:
    """Persist bytes content-addressed by sha256. Idempotent.

    Plugin artifacts use the default `.zip`; media bytes use `.bin` so the two
    namespaces never collide on disk.
    """
    dest = _path_for(sha256, ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
    return dest


def delete(sha256: str, ext: str = ".zip") -> bool:
    """Remove bytes from disk. Idempotent; returns True if a file was removed."""
    path = _path_for(sha256, ext)
    if not path.exists():
        return False
    path.unlink()
    return True


def read(sha256: str, ext: str = ".zip") -> bytes:
    path = _path_for(sha256, ext)
    if not path.exists():
        raise FileNotFoundError(f"artifact {sha256[:12]}… not found on disk")
    return path.read_bytes()
