"""Reviews written from inside the pane — no redirect, no marketplace account.

The old pane linked out to the marketplace website to write a review, which
sent the owner to a service that has no idea who they are. Instead this plugin
speaks the marketplace's Luna handshake itself:

1. **enroll** (lazy, once per marketplace host) — POST ``{origin}/api/luna/enroll``
   with a vault-stored ``install_id``; the reply carries ``{tenant, secret}``,
   which go straight back into the vault and are never logged or returned to
   the browser.
2. **sign** — every review read/write is
   ``HMAC_SHA256(secret, f"{ts}." + raw_body)`` in
   ``X-Luna-Tenant / X-Luna-Ts / X-Luna-Signature``.

So the *install* is the identity. It can only ever speak for itself, and the
pane knows which review is the owner's own because the marketplace answers the
signed read with ``is_mine``.

All credential names start with ``plugin_marketplace_ui.`` so they are
self-owned by this plugin (no vault grant, no approval prompt). This is a
separate identity from core plugin-marketplace's sync enrollment — a plugin
cannot read another plugin's credentials, and asking the owner to approve that
for a review box would be worse than holding our own install id.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import anyio

INSTALL_ID_CRED = "plugin_marketplace_ui.install_id"
HTTP_TIMEOUT = 15.0


class ReviewError(Exception):
    """Failure with a message that is safe to show in the pane."""

    def __init__(self, message: str, *, status: int = 502):
        super().__init__(message)
        self.status = status


def origin_of(marketplace_url: str) -> str:
    """``https://host/mp/official`` → ``https://host`` (the API origin)."""
    parts = urlsplit(marketplace_url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ReviewError(f"Not a valid marketplace URL: {marketplace_url}", status=400)
    return f"{parts.scheme}://{parts.netloc}"


def _cred(kind: str, origin: str) -> str:
    return f"plugin_marketplace_ui.mp_{kind}.{urlsplit(origin).netloc}"


async def _install_id(vault) -> str:
    try:
        return (await vault.get_credential(INSTALL_ID_CRED)).value
    except KeyError:
        install_id = uuid.uuid4().hex
        await vault.store_credential(
            INSTALL_ID_CRED,
            install_id,
            kind="api_key",
            metadata={"purpose": "marketplace review identity"},
        )
        return install_id


def _post(url: str, raw: bytes, headers: dict[str, str]) -> dict:
    req = Request(  # noqa: S310 - owner-configured marketplace URL
        url,
        data=raw,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read() or b"{}")


def _net_error(e: Exception) -> ReviewError:
    if isinstance(e, HTTPError):
        detail = ""
        try:
            payload = json.loads(e.read() or b"{}")
            detail = payload.get("detail") or ""
            if isinstance(detail, dict):
                detail = detail.get("message") or ""
        except Exception:  # noqa: BLE001
            pass
        return ReviewError(detail or f"The marketplace replied {e.code}.", status=e.code)
    if isinstance(e, URLError):
        return ReviewError(f"Could not reach the marketplace ({e.reason}).")
    return ReviewError(f"{type(e).__name__}: {e}")


def _luna_meta(ctx) -> tuple[str, str]:
    name = ""
    try:
        name = ctx.get_env("LUNA_HOST_NAME") or ctx.get_env("LUNA_OWNER_USERNAME") or ""
    except Exception:  # noqa: BLE001
        pass
    try:
        import luna

        version = getattr(luna, "__version__", "")
    except Exception:  # noqa: BLE001
        version = ""
    return name or "Luna", version


async def _credentials(ctx, origin: str) -> tuple[str, str]:
    """(tenant, secret) for this marketplace, enrolling on first use."""
    vault = ctx.vault
    if vault is None:
        raise ReviewError("No vault is available, so reviews can't be signed.", status=503)
    try:
        tenant = (await vault.get_credential(_cred("tenant", origin))).value
        secret = (await vault.get_credential(_cred("secret", origin))).value
        return tenant, secret
    except KeyError:
        pass

    install_id = await _install_id(vault)
    luna_name, luna_version = _luna_meta(ctx)
    payload = json.dumps({
        "install_id": install_id,
        "luna_name": luna_name,
        "luna_version": luna_version,
    }).encode()
    try:
        resp = await anyio.to_thread.run_sync(
            lambda: _post(f"{origin}/api/luna/enroll", payload, {})
        )
    except Exception as e:  # noqa: BLE001
        raise _net_error(e) from e

    tenant, secret = resp.get("tenant"), resp.get("secret")
    if not tenant or not secret:
        # Re-enroll of a known install never re-issues the secret. That only
        # happens if our stored secret was deleted — a fresh id recovers.
        raise ReviewError(
            "This Luna is already enrolled with that marketplace but its review "
            "key is missing. Remove and re-add the marketplace to reset it.",
            status=409,
        )
    for kind, value in (("tenant", tenant), ("secret", secret)):
        await vault.store_credential(
            _cred(kind, origin),
            value,
            kind="api_key",
            metadata={"purpose": f"marketplace review {kind}", "origin": origin},
        )
    return tenant, secret


async def signed_call(ctx, marketplace_url: str, path: str, payload: dict[str, Any]) -> dict:
    """Signed POST to ``{origin}{path}``. Never returns or logs the secret."""
    origin = origin_of(marketplace_url)
    tenant, secret = await _credentials(ctx, origin)
    raw = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()
    headers = {"X-Luna-Tenant": tenant, "X-Luna-Ts": ts, "X-Luna-Signature": sig}
    try:
        return await anyio.to_thread.run_sync(lambda: _post(f"{origin}{path}", raw, headers))
    except Exception as e:  # noqa: BLE001
        raise _net_error(e) from e


def installed_version(plugin_name: str) -> str:
    """The version of `plugin_name` running in *this* Luna, or "".

    Drives `verified_install` on the marketplace side: a review from an owner
    who actually has the plugin is worth more than one from a browser.
    """
    try:
        from luna.plugins.loader import get_plugin_registry

        for lp in get_plugin_registry().all():
            if lp.manifest.name == plugin_name:
                return lp.manifest.version or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


async def trusted_origins(ctx) -> set[str]:
    """Origins of the marketplaces the owner added — the allowlist for the
    proxy. Without this the pane could aim signed requests at any host."""
    urls: list[str] = []
    try:
        from sqlalchemy import select

        from plugins.plugin_marketplace.models import MarketplaceSource

        async with ctx.db_session_factory() as s:
            urls = [r.url for r in (await s.execute(select(MarketplaceSource))).scalars().all()]
    except Exception:  # noqa: BLE001
        try:
            from sqlalchemy import text

            async with ctx.db_session_factory() as s:
                rows = (await s.execute(text("SELECT url FROM plugin_marketplace_sources"))).all()
            urls = [r[0] for r in rows]
        except Exception:  # noqa: BLE001
            return set()
    out: set[str] = set()
    for u in urls:
        try:
            out.add(origin_of(u))
        except ReviewError:
            continue
    return out
