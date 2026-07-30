"""A plugin's code must report the same version its package declares.

`luna-plugin.toml` is what the marketplace publishes and what Luna's upgrade
check compares against; the `PluginManifest(version=...)` in the code is what a
running Luna reports as installed (the loaded manifest wins — Luna 008.992).
When those drift, an update lands new code on disk, reports success, and the
catalog still offers the same update forever — the sticky "1 update available"
banner. This test is the gate that keeps them in step.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "marketplace-src"


def _toml_version(pkg: Path) -> str:
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (pkg / "luna-plugin.toml").read_text())
    assert m, f"{pkg.name}/luna-plugin.toml has no version"
    return m.group(1)


def _code_version(pkg: Path) -> str | None:
    """The manifest version as written in `__init__.py`, or None when the file
    reads it out of `luna-plugin.toml` at import time (can't drift by
    construction)."""
    src = (pkg / "__init__.py").read_text()
    if "luna-plugin.toml" in src:
        return None
    tree = ast.parse(src)
    consts = {
        t.id: n.value.value
        for n in tree.body
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
        for t in n.targets
        if isinstance(t, ast.Name)
    }
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "PluginManifest"):
            continue
        for kw in node.keywords:
            if kw.arg != "version":
                continue
            if isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
            if isinstance(kw.value, ast.Name):
                return str(consts.get(kw.value.id, ""))
    return ""


def test_every_plugin_manifest_version_matches_its_toml():
    pkgs = sorted(
        p for p in SRC.iterdir()
        if (p / "luna-plugin.toml").exists() and (p / "__init__.py").exists()
    )
    assert pkgs, "no plugin sources found"
    drift = []
    for pkg in pkgs:
        code = _code_version(pkg)
        if code is None:
            continue
        toml = _toml_version(pkg)
        if code != toml:
            drift.append(f"{pkg.name}: toml={toml} code={code or '<unresolved>'}")
    assert not drift, "manifest version drifts from luna-plugin.toml:\n" + "\n".join(drift)
