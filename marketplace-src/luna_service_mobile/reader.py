"""Turns queued bus items into feed events, reading Luna's own tables where needed.

Plain SQL on `conversations` and `messages` (Luna's schema) through the plugin's session factory,
so the plugin imports nothing from Luna core. Every feed event carries a stable `event_id`
(message or approval id where one exists), which makes retries and boot snapshots idempotent on
the control plane.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

log = logging.getLogger("luna-service-mobile.reader")

PREVIEW_CHARS = 200
SNAPSHOT_LIMIT = 100
FEED_ROLES = ("user", "assistant")


def preview(content: Any, extra: dict | None = None) -> str | None:
    s = " ".join(str(content or "").split())
    if not s and extra:
        if extra.get("kind") == "card":
            s = "Card"
        elif extra.get("title"):
            s = str(extra["title"])
    if not s:
        return None
    return s if len(s) <= PREVIEW_CHARS else s[: PREVIEW_CHARS - 1] + "…"


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
    return str(v)


def _extra(v: Any) -> dict:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        import json
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {}
        except ValueError:
            return {}
    return {}


class Reader:
    """Stateful: remembers the last assistant row sent per conversation, so a finished turn sends
    only the rows it wrote."""

    def __init__(self, session_factory):
        self._sf = session_factory
        self._last_sent: dict[str, str] = {}   # conversation_id -> created_at iso of last row sent

    async def title(self, conversation_id: str) -> str | None:
        async with self._sf() as s:
            row = (await s.execute(
                text("SELECT title FROM conversations WHERE id = :c"), {"c": conversation_id},
            )).first()
        return row[0] if row else None

    async def rows_after(self, conversation_id: str, after: str | None, limit: int = 20) -> list[dict]:
        q = ("SELECT id, role, content, extra, created_at FROM messages "
             "WHERE conversation_id = :c AND role IN ('user', 'assistant')")
        params: dict[str, Any] = {"c": conversation_id, "n": limit}
        if after:
            q += " AND created_at > :after"
            params["after"] = datetime.fromisoformat(after)
        q += " ORDER BY created_at DESC LIMIT :n"
        async with self._sf() as s:
            rows = (await s.execute(text(q), params)).all()
        out = [
            {"id": str(r[0]), "role": r[1], "content": r[2], "extra": _extra(r[3]), "created_at": _iso(r[4])}
            for r in rows
        ]
        out.reverse()
        return out

    async def resolve(self, items: list[tuple[str, dict]]) -> list[dict]:
        events: list[dict] = []
        titles: dict[str, str | None] = {}

        async def title_of(conv: str) -> str | None:
            if conv not in titles:
                try:
                    titles[conv] = await self.title(conv)
                except Exception:  # noqa: BLE001
                    titles[conv] = None
            return titles[conv]

        for kind, p in items:
            try:
                conv = str(p["conversation_id"]) if p.get("conversation_id") else None
                if kind == "user_message" and conv:
                    events.append({
                        "type": "message", "event_id": str(p.get("message_id") or uuid.uuid4()),
                        "conversation_id": conv, "message_id": p.get("message_id"), "role": "user",
                        "created_at": (p.get("ambient_context") or {}).get("occurred_at"),
                        "preview": preview(p.get("content")), "title": await title_of(conv),
                    })
                elif kind == "created" and conv:
                    extra = {k: p.get(k) for k in ("kind", "title") if p.get(k)}
                    events.append({
                        "type": "message", "event_id": str(p.get("message_id") or uuid.uuid4()),
                        "conversation_id": conv, "message_id": p.get("message_id"),
                        "role": p.get("role") or "assistant", "created_at": p.get("created_at"),
                        "preview": preview(p.get("content"), extra), "title": await title_of(conv),
                    })
                    if p.get("created_at"):
                        self._last_sent[conv] = max(self._last_sent.get(conv, ""), str(p["created_at"]))
                elif kind == "turn.started" and conv:
                    events.append({"type": "turn.started", "event_id": str(uuid.uuid4()),
                                   "conversation_id": conv, "source": p.get("source"),
                                   "created_at": datetime.now(timezone.utc).isoformat()})
                elif kind == "turn.ended" and conv:
                    # The reply's rows are committed before this event fires (plugin_api stream finally).
                    rows = await self.rows_after(conv, self._last_sent.get(conv))
                    if conv not in self._last_sent:
                        rows = [r for r in rows if r["role"] == "assistant"][-1:]
                    for r in rows:
                        if r["role"] != "assistant":
                            continue
                        events.append({
                            "type": "message", "event_id": r["id"], "conversation_id": conv,
                            "message_id": r["id"], "role": "assistant", "created_at": r["created_at"],
                            "preview": preview(r["content"], r["extra"]), "title": await title_of(conv),
                        })
                    if rows:
                        self._last_sent[conv] = rows[-1]["created_at"]
                    events.append({"type": "turn.ended", "event_id": str(uuid.uuid4()),
                                   "conversation_id": conv,
                                   "created_at": datetime.now(timezone.utc).isoformat()})
                elif kind == "approval.requested" and p.get("id"):
                    events.append({"type": "approval.requested", "event_id": f"req-{p['id']}",
                                   "approval_id": str(p["id"]), "conversation_id": conv,
                                   "summary": preview(p.get("summary"))})
                elif kind == "approval.decided" and p.get("id"):
                    events.append({"type": "approval.decided", "event_id": f"dec-{p['id']}",
                                   "approval_id": str(p["id"]), "conversation_id": conv,
                                   "decision": p.get("decision")})
                elif kind == "conversation" and conv:
                    events.append({"type": "conversation", "event_id": str(uuid.uuid4()),
                                   "conversation_id": conv, "kind": p.get("kind"), "state": p.get("state"),
                                   "title": await title_of(conv)})
                elif kind == "snapshot":
                    events.extend(await self.snapshot())
            except Exception:  # noqa: BLE001
                log.exception("could not resolve %s", kind)
        return events

    async def snapshot(self) -> list[dict]:
        """Recent conversations with their last user/assistant message: fills the feed on boot and
        heals anything missed while the machine was down."""
        async with self._sf() as s:
            convs = (await s.execute(text(
                "SELECT id, title, kind, state, created_at FROM conversations "
                "WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT :n"
            ), {"n": SNAPSHOT_LIMIT})).all()
        events: list[dict] = []
        for cid, title, kind, state, created in convs:
            conv = str(cid)
            events.append({"type": "conversation", "event_id": f"snap-{conv}-{_iso(created)}",
                           "conversation_id": conv, "title": title, "kind": kind, "state": state,
                           "created_at": _iso(created)})
            last = await self.rows_after(conv, None, limit=1)
            for r in last:
                events.append({
                    "type": "message", "event_id": r["id"], "conversation_id": conv,
                    "message_id": r["id"], "role": r["role"], "created_at": r["created_at"],
                    "preview": preview(r["content"], r["extra"]), "title": title,
                })
                self._last_sent[conv] = r["created_at"]
        return events
