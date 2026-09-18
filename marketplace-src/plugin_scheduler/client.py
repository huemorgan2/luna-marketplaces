"""HMAC-signed HTTP client for the scheduler-service account API.

Config is vault-first (`plugin_scheduler.service_url` / `.account_id` /
`.secret`), with env fallbacks (`LUNA_SCHEDULER_SERVICE_URL` /
`LUNA_SCHEDULER_ACCOUNT_ID` / `LUNA_SCHEDULER_SECRET`) for local dev. On
401/403 the caller should surface "reconnect needed".
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from .hmac import HDR_SIG, HDR_TS, sign

log = logging.getLogger("plugin-scheduler.client")

VAULT_SERVICE_URL = "plugin_scheduler.service_url"
VAULT_ACCOUNT_ID = "plugin_scheduler.account_id"
VAULT_SECRET = "plugin_scheduler.secret"

ENV_SERVICE_URL = "LUNA_SCHEDULER_SERVICE_URL"
ENV_ACCOUNT_ID = "LUNA_SCHEDULER_ACCOUNT_ID"
ENV_SECRET = "LUNA_SCHEDULER_SECRET"

TIMEOUT_S = 10.0


class NotConnected(RuntimeError):
    """No scheduler credentials configured (vault or env)."""


async def _vault_get(ctx, name: str) -> str | None:
    vault = getattr(ctx, "vault", None)
    if vault is None:
        return None
    try:
        cred = await vault.get_credential(name)
    except KeyError:
        return None
    except Exception as exc:  # noqa: BLE001 — vault down must not crash tools
        log.warning("vault read failed for %s: %s", name, exc)
        return None
    return getattr(cred, "value", None) or (cred if isinstance(cred, str) else None)


async def get_config(ctx) -> dict[str, str] | None:
    """Resolve {service_url, account_id, secret}; None when not configured."""
    service_url = await _vault_get(ctx, VAULT_SERVICE_URL) or os.environ.get(ENV_SERVICE_URL, "").strip()
    account_id = await _vault_get(ctx, VAULT_ACCOUNT_ID) or os.environ.get(ENV_ACCOUNT_ID, "").strip()
    secret = await _vault_get(ctx, VAULT_SECRET) or os.environ.get(ENV_SECRET, "").strip()
    if not (service_url and account_id and secret):
        return None
    return {"service_url": service_url.rstrip("/"), "account_id": account_id, "secret": secret}


async def _send(cfg: dict[str, str], method: str, path: str, body: dict | None) -> httpx.Response:
    raw = json.dumps(body) if body is not None else ""
    ts, sig = sign(cfg["secret"], raw)
    headers = {HDR_TS: ts, HDR_SIG: sig, "content-type": "application/json"}
    url = f"{cfg['service_url']}/accounts/{cfg['account_id']}{path}"
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
        return await c.request(method, url, content=raw or None, headers=headers)


async def _request(ctx, method: str, path: str, body: dict | None = None) -> Any:
    """Signed request to /accounts/{account_id}{path}. Raises NotConnected /
    httpx errors; 4xx returns the parsed error body via SchedulerError.

    On 401/403 (secret rotated away from us, or account deleted server-side —
    both present as "bad signature") a hosted Luna self-repairs once: rotate-
    connect through the control plane, store fresh creds, retry the call."""
    cfg = await get_config(ctx)
    if cfg is None:
        raise NotConnected(
            "scheduler not connected — configure it in Settings → Scheduler"
        )
    resp = await _send(cfg, method, path, body)

    def _detail(r: httpx.Response) -> str:
        try:
            return str(r.json().get("detail", r.text))
        except Exception:  # noqa: BLE001
            return r.text

    # "bad signature" only — a 403 "account disabled" is not repairable by
    # rotating and must surface as-is.
    if resp.status_code in (401, 403) and "signature" in _detail(resp).lower():
        from . import provision  # late import — provision imports this module

        repaired = await provision.repair(ctx)
        if repaired.get("state") == "provisioned":
            cfg = await get_config(ctx)
            if cfg is not None:
                resp = await _send(cfg, method, path, body)

    if resp.status_code >= 400:
        raise SchedulerError(resp.status_code, _detail(resp))
    return resp.json()


class SchedulerError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def create_trigger(ctx, *, name: str, expr: str, action_type: str, target: str,
                         inputs: dict | None = None, timezone: str | None = None,
                         max_runs: int | None = None, unique_name: bool = False,
                         purpose: str | None = None,
                         created_by: str = "plugin-scheduler") -> dict:
    body = {"name": name, "expr": expr, "action_type": action_type, "target": target,
            "created_by": created_by}
    if inputs is not None:
        body["inputs"] = inputs
    if timezone:
        body["timezone"] = timezone
    if max_runs is not None:
        body["max_runs"] = max_runs
    if unique_name:
        # service >= 0.3.0 upserts by (account, name); older services ignore
        # the flag and create — callers keep their own list-before-create.
        body["unique_name"] = True
    if purpose:
        body["purpose"] = purpose
    return await _request(ctx, "POST", "/triggers", body)


async def list_triggers(ctx) -> list[dict]:
    return (await _request(ctx, "GET", "/triggers"))["triggers"]


async def update_trigger(ctx, trigger_id: str, *, name: str | None = None,
                         expr: str | None = None, target: str | None = None,
                         inputs: dict | None = None, timezone: str | None = None) -> dict:
    body: dict[str, Any] = {}
    if name is not None:
        body["name"] = name
    if expr is not None:
        body["expr"] = expr
    if target is not None:
        body["target"] = target
    if inputs is not None:
        body["inputs"] = inputs
    if timezone is not None:
        body["timezone"] = timezone
    return await _request(ctx, "PATCH", f"/triggers/{trigger_id}", body)


async def delete_trigger(ctx, trigger_id: str) -> dict:
    return await _request(ctx, "DELETE", f"/triggers/{trigger_id}")


async def pause_trigger(ctx, trigger_id: str) -> dict:
    return await _request(ctx, "POST", f"/triggers/{trigger_id}/pause")


async def resume_trigger(ctx, trigger_id: str) -> dict:
    return await _request(ctx, "POST", f"/triggers/{trigger_id}/resume")


async def run_now(ctx, trigger_id: str) -> dict:
    return await _request(ctx, "POST", f"/triggers/{trigger_id}/run-now")


async def list_fires(ctx, limit: int = 50) -> list[dict]:
    return (await _request(ctx, "GET", f"/fires?limit={limit}"))["fires"]


async def parse_preview(ctx, expr: str, timezone: str | None = None) -> dict:
    body: dict[str, Any] = {"expr": expr}
    if timezone:
        body["timezone"] = timezone
    return await _request(ctx, "POST", "/parse", body)
