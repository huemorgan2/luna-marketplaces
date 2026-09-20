"""http_request — generic HTTP client tool for API calls (005.902).

Thin httpx wrapper: returns {status, headers, body}. JSON responses are parsed
into structured data; large bodies are truncated. Core logic takes an
`httpx.AsyncClient` for testability.
"""

from __future__ import annotations

import json as _json
from typing import Any

import httpx

from .safety import blocked_reason

DEFAULT_TIMEOUT = 30.0
MAX_BODY = 100_000  # chars
MAX_REDIRECTS = 5
USER_AGENT = "Luna/1.0 (AI Agent; +https://github.com/huemorgan/luna)"
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


async def run_request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: Any = None,
    timeout: int = 30,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Make an HTTP request. Never raises — returns an error dict on failure."""
    method = (method or "GET").strip().upper()
    if method not in _METHODS:
        return {"error": "invalid method", "detail": f"Use one of {sorted(_METHODS)}"}
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return {"error": "invalid url", "detail": "URL must start with http:// or https://", "url": url}
    req_headers = {"User-Agent": USER_AGENT, **(headers or {})}
    content: bytes | None = None
    json_body: Any = None
    if body is not None:
        if isinstance(body, (dict, list)):
            json_body = body
        else:
            content = str(body).encode("utf-8")

    try:
        to = float(timeout) if timeout else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        to = DEFAULT_TIMEOUT

    # 068/phase002: default to the per-loop shared client; the caller's timeout
    # travels per-request. An injected client (tests) is used as-is. Not closed.
    if client is None:
        from .shared import shared_client

        client = shared_client()
    try:
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            blocked = blocked_reason(current)
            if blocked:
                return {"error": "blocked", "detail": blocked, "url": current}
            resp = await client.request(
                method, current, headers=req_headers, content=content, json=json_body,
                timeout=to, follow_redirects=False,
            )
            # Do not automatically replay a write against a redirect target.
            if method not in {"GET", "HEAD"} or resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("location")
            if not location:
                return {"error": "request failed", "detail": "redirect has no location", "url": current, "method": method}
            current = str(resp.url.join(location))
            if not current.lower().startswith(("http://", "https://")):
                return {"error": "blocked", "detail": "redirect uses an unsupported scheme", "url": current}
            if hop == MAX_REDIRECTS:
                return {"error": "request failed", "detail": "too many redirects", "url": current, "method": method}
        text = resp.text
        parsed: Any = None
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype:
            try:
                parsed = resp.json()
            except (ValueError, _json.JSONDecodeError):
                parsed = None
        structured_too_large = parsed is not None and len(_json.dumps(parsed)) > MAX_BODY
        out: dict[str, Any] = {
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "body": text[:MAX_BODY],
            "truncated": len(text) > MAX_BODY or structured_too_large,
            "url": str(resp.url),
        }
        if parsed is not None and not structured_too_large:
            out["json"] = parsed
        return out
    except httpx.HTTPError as exc:
        return {"error": "request failed", "detail": str(exc), "url": url, "method": method}
