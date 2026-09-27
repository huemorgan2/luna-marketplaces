"""luna-service-mobile: reader (Luna's tables → feed events) and sender (batching, retries).

Runs without Luna: the modules are loaded by path and the tables are created in SQLite with
Luna's column names. The plugin class itself is exercised by test_plugin_load.py, which needs
Luna on the path.

    pytest tests/026-luna-service-mobile
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PKG = Path(__file__).resolve().parents[2] / "marketplace-src" / "luna_service_mobile"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"lsm_{name}", PKG / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


reader_mod = _load("reader")
sender_mod = _load("sender")
T0 = datetime(2026, 9, 27, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
async def sf():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.execute(text("CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT, kind TEXT, "
                             "state TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, deleted_at TIMESTAMP)"))
        await c.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, "
                             "content TEXT, extra JSON, created_at TIMESTAMP)"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _conv(sf, title="Trip", deleted=False, at=T0):
    cid = str(uuid.uuid4())
    async with sf() as s:
        await s.execute(text("INSERT INTO conversations VALUES (:i, :t, 'building', 'building', :a, :a, :d)"),
                        {"i": cid, "t": title, "a": at, "d": at if deleted else None})
        await s.commit()
    return cid


async def _msg(sf, cid, role, content, at, extra=None):
    mid = str(uuid.uuid4())
    async with sf() as s:
        await s.execute(text("INSERT INTO messages VALUES (:i, :c, :r, :t, :e, :a)"),
                        {"i": mid, "c": cid, "r": role, "t": content, "e": json.dumps(extra) if extra else None, "a": at})
        await s.commit()
    return mid


def test_preview_clamps_and_describes_empty_rows():
    p = reader_mod.preview
    assert p("  hello \n  world ") == "hello world"
    assert len(p("x" * 500)) == 200 and p("x" * 500).endswith("…")
    assert p("", {"kind": "card"}) == "Card"
    assert p("", {"title": "Daily digest"}) == "Daily digest"
    assert p("", None) is None


async def test_turn_ended_sends_only_the_rows_the_turn_wrote(sf):
    r = reader_mod.Reader(sf)
    cid = await _conv(sf)
    await _msg(sf, cid, "user", "old question", T0)
    old = await _msg(sf, cid, "assistant", "old answer", T0 + timedelta(seconds=1))
    # First turn this process sees: only the latest assistant row (not the whole history).
    ev = await r.resolve([("turn.ended", {"conversation_id": cid})])
    msgs = [e for e in ev if e["type"] == "message"]
    assert [m["event_id"] for m in msgs] == [old]
    assert ev[-1]["type"] == "turn.ended"
    assert msgs[0]["title"] == "Trip" and msgs[0]["preview"] == "old answer"

    await _msg(sf, cid, "user", "next", T0 + timedelta(seconds=5))
    a1 = await _msg(sf, cid, "assistant", "part one", T0 + timedelta(seconds=6))
    a2 = await _msg(sf, cid, "assistant", "", T0 + timedelta(seconds=7), extra={"kind": "card"})
    ev = await r.resolve([("turn.ended", {"conversation_id": cid})])
    assert [e["event_id"] for e in ev if e["type"] == "message"] == [a1, a2]
    assert [e["preview"] for e in ev if e["type"] == "message"] == ["part one", "Card"]


async def test_user_message_created_approvals_and_turn_started(sf):
    r = reader_mod.Reader(sf)
    cid = await _conv(sf, title="Inbox")
    ev = await r.resolve([
        ("user_message", {"conversation_id": cid, "message_id": "m1", "content": "hi there",
                          "ambient_context": {"occurred_at": T0.isoformat()}}),
        ("turn.started", {"conversation_id": cid, "source": "chat"}),
        ("created", {"conversation_id": cid, "message_id": "m2", "content": "", "role": "user",
                     "kind": "muted", "title": "Daily digest", "created_at": T0.isoformat()}),
        ("approval.requested", {"id": "ap1", "conversation_id": cid, "summary": "Send email"}),
        ("approval.decided", {"id": "ap1", "decision": "approved"}),
        ("conversation", {"conversation_id": cid, "kind": "ops", "state": "identify"}),
    ])
    types = [e["type"] for e in ev]
    assert types == ["message", "turn.started", "message", "approval.requested", "approval.decided", "conversation"]
    assert ev[0]["role"] == "user" and ev[0]["preview"] == "hi there" and ev[0]["title"] == "Inbox"
    assert ev[2]["preview"] == "Daily digest"
    assert ev[3]["event_id"] == "req-ap1" and ev[4]["event_id"] == "dec-ap1"
    assert ev[4]["conversation_id"] is None  # the server fills it from the request
    assert ev[5]["kind"] == "ops"


async def test_snapshot_lists_live_conversations_with_last_message(sf):
    r = reader_mod.Reader(sf)
    a = await _conv(sf, title="A")
    b = await _conv(sf, title="B")
    await _conv(sf, title="gone", deleted=True)
    await _msg(sf, a, "user", "q", T0)
    last = await _msg(sf, a, "assistant", "latest", T0 + timedelta(seconds=1))
    await _msg(sf, a, "tool", "raw tool output", T0 + timedelta(seconds=2))
    ev = await r.resolve([("snapshot", {})])
    convs = {e["conversation_id"] for e in ev if e["type"] == "conversation"}
    assert convs == {a, b}
    msgs = [e for e in ev if e["type"] == "message"]
    assert [(m["event_id"], m["preview"]) for m in msgs] == [(last, "latest")]
    # Snapshot ids are stable, so a restart re-sends the same ids.
    ev2 = await reader_mod.Reader(sf).resolve([("snapshot", {})])
    assert sorted(e["event_id"] for e in ev) == sorted(e["event_id"] for e in ev2)


async def test_bad_items_are_skipped_not_raised(sf):
    r = reader_mod.Reader(sf)
    ev = await r.resolve([("turn.ended", {"conversation_id": "not-a-conversation"}),
                          ("user_message", {}), ("mystery", {"x": 1})])
    assert [e["type"] for e in ev] == ["turn.ended"]


async def test_sender_batches_and_retries_then_drops_on_401(monkeypatch):
    monkeypatch.setattr(sender_mod, "BURST_WAIT", 0.01)
    slept = []

    async def fast_sleep(s):
        slept.append(s)

    posted, statuses = [], [503, 0, 202, 401]

    async def post(events):
        posted.append(list(events))
        st = statuses.pop(0)
        if st == 0:
            raise OSError("connection refused")
        return st

    async def resolve(items):
        return [{"type": "turn.started", "event_id": str(i), "conversation_id": "c"} for i in items]

    s = sender_mod.Sender("https://cp.example/proxy/", "tok", resolve, post)
    assert s._url == "https://cp.example/proxy/api/agent/chats/events"
    for i in range(3):
        s.put(i)
    monkeypatch.setattr(sender_mod.asyncio, "sleep", fast_sleep)
    await s.drain_once()
    assert len(posted) == 3 and all(len(p) == 3 for p in posted)   # 503, error, then 202
    assert slept == [1.0, 2.0]
    s.put(9)
    await s.drain_once()
    assert len(posted) == 4 and statuses == []                        # 401: dropped, no retry


async def test_sender_queue_is_bounded(monkeypatch):
    monkeypatch.setattr(sender_mod, "MAX_QUEUE", 5)

    async def resolve(items):
        return []

    s = sender_mod.Sender("http://x", "t", resolve, None)
    for i in range(20):
        s.put(i)
    assert s._queue.qsize() == 5
    items = [s._queue.get_nowait() for _ in range(5)]
    assert items == [15, 16, 17, 18, 19]  # newest kept


async def test_sender_loop_runs_in_background(monkeypatch):
    monkeypatch.setattr(sender_mod, "BURST_WAIT", 0.01)
    got = asyncio.Event()

    async def post(events):
        got.set()
        return 202

    async def resolve(items):
        return [{"type": "turn.started", "event_id": "x", "conversation_id": "c"}]

    s = sender_mod.Sender("http://x", "t", resolve, post)
    s.start()
    s.put(("turn.started", {}))
    await asyncio.wait_for(got.wait(), 2)
    await s.stop()
