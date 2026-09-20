"""Self-provisioning against the luna-service control plane (034.1 pattern).

When no config exists and this is a hosted Luna (`LUNA_GATEWAY_URL` +
`LUNA_GATEWAY_TOKEN` present), call the control plane's connect endpoint and
store the returned credentials in the vault. OSS/self-hosted Lunas get the
manual-config state instead — no calls are made.
"""

from __future__ import annotations

import logging
import os

import httpx

from . import client

log = logging.getLogger("plugin-scheduler.provision")

ENV_GATEWAY_URL = "LUNA_GATEWAY_URL"
ENV_GATEWAY_TOKEN = "LUNA_GATEWAY_TOKEN"
CONNECT_PATH = "/api/agent/scheduler/connect"


async def store_config(ctx, service_url: str, account_id: str, secret: str) -> None:
    vault = getattr(ctx, "vault", None)
    if vault is None:
        raise RuntimeError("vault unavailable — cannot store scheduler credentials")
    await vault.store_credential(client.VAULT_SERVICE_URL, service_url.rstrip("/"))
    await vault.store_credential(client.VAULT_ACCOUNT_ID, account_id)
    await vault.store_credential(client.VAULT_SECRET, secret)


async def _connect(ctx, *, rotate: bool) -> dict:
    """Call the control plane's connect and store returned creds.
    Returns {state: provisioned|manual|error, detail?}."""
    gateway = os.environ.get(ENV_GATEWAY_URL, "").strip().rstrip("/")
    token = os.environ.get(ENV_GATEWAY_TOKEN, "").strip()
    if not (gateway and token):
        return {"state": "manual",
                "detail": "requires a hosted Luna or a self-run scheduler-service"}

    try:
        async with httpx.AsyncClient(timeout=15) as c:
            resp = await c.post(f"{gateway}{CONNECT_PATH}",
                                headers={"authorization": f"Bearer {token}"},
                                json={"rotate": True} if rotate else None)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("secret"):
            # The control plane's connect is idempotent and only reveals the
            # secret on creation (or on rotate). Account exists but our vault
            # copy is gone — repair() recovers by rotating.
            return {"state": "error",
                    "detail": "account exists but no secret was returned — "
                              "rotate the scheduler account secret and reconnect"}
        await store_config(ctx, data["service_url"], data["account_id"], data["secret"])
        return {"state": "provisioned"}
    except Exception as exc:  # noqa: BLE001 — provision is best-effort on load
        log.warning("scheduler self-provision failed: %s", exc)
        return {"state": "error", "detail": str(exc)}


async def ensure_connected(ctx) -> dict:
    """Returns {state: connected|provisioned|manual|error, detail?}."""
    if await client.get_config(ctx) is not None:
        return {"state": "connected"}
    return await _connect(ctx, rotate=False)


async def repair(ctx) -> dict:
    """Recover credentials the service no longer accepts (account deleted, or
    secret rotated away from us — both surface as 401/403 "bad signature").

    Hosted only: re-connect with ``rotate`` — the control plane re-creates a
    missing account (fresh secret on creation) or rotates the existing one —
    then store the new credentials. Ignores the vault's current (stale) config
    on purpose. Safe: the device token proves this is the account's machine,
    and rotation only invalidates the secret that already doesn't work."""
    log.info("scheduler credentials rejected — attempting self-repair (rotate)")
    return await _connect(ctx, rotate=True)
