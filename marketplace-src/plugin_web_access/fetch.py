"""web_fetch — GET a URL and return readable text (005.902).

Content extraction uses the stdlib HTMLParser to strip script/style/nav chrome
and collapse to plain text — zero extra dependencies. The plan names
`trafilatura` for higher-quality markdown extraction; that's a drop-in future
upgrade (see NOTES.md). Core logic takes an `httpx.AsyncClient` for testability.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

import httpx

from .safety import blocked_reason

DEFAULT_TIMEOUT = 30.0
MAX_CONTENT = 50_000  # chars
USER_AGENT = "Luna/1.0 (AI Agent; +https://github.com/huemorgan/luna)"
_DROP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "nav", "footer", "aside"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "tr"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        # Title lives inside <head> (a dropped tag), so capture it before the
        # skip check; everything else inside dropped tags is ignored.
        if self._in_title:
            self.title += data
            return
        if self._skip_depth > 0:
            return
        text = data.strip()
        if text:
            self._chunks.append(text + " ")

    def text(self) -> str:
        raw = "".join(self._chunks)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        return raw.strip()


def extract_readable(html: str) -> tuple[str, str]:
    """Return (title, text) from raw HTML using the stdlib parser."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 — never let malformed HTML crash a fetch
        pass
    return parser.title.strip(), parser.text()


async def run_fetch(url: str, *, client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch a URL and return readable content. Never raises — returns an error
    dict on timeout / bad status / network error so the agent can relay it."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"error": "invalid url", "detail": "URL must start with http:// or https://", "url": url}
    blocked = blocked_reason(url)
    if blocked:
        return {"error": "blocked", "detail": blocked, "url": url}

    # 068/phase002: default to the per-loop shared client (warm connections);
    # an injected client (tests) is used as-is. Neither is closed here.
    if client is None:
        from .shared import shared_client

        client = shared_client()
    try:
        resp = await client.get(
            url, timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        if "html" in ctype or ctype == "":
            title, text = extract_readable(resp.text)
        else:
            title, text = "", resp.text
        truncated = len(text) > MAX_CONTENT
        return {
            "url": str(resp.url),
            "title": title,
            "content": text[:MAX_CONTENT],
            "content_length": len(text),
            "truncated": truncated,
            "status": resp.status_code,
        }
    except httpx.HTTPStatusError as exc:
        return {"error": "fetch failed", "detail": f"HTTP {exc.response.status_code}", "url": url}
    except httpx.HTTPError as exc:
        return {"error": "fetch failed", "detail": str(exc), "url": url}
