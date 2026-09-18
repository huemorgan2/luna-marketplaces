"""plugin-scheduler routes — fire ingress + settings-tab API + settings UI.

Fire ingress contract (with scheduler-service, relayed by luna-service):
verify HMAC over the raw bytes → dedupe on fire_id → record → 200 fast; the
actual work (agent turn / playbook run) happens in a background task, never
in-request (the service retries slow responses — in-request work would
double-run).

Deviation from plan 035 (documented in plans/001-mvp): current Luna has no
`playbook.run.requested` subscription and no synthetic `message.received`
inbox, so fires integrate through what exists today — the playbooks plugin's
`playbook_run` tool handler, and `ctx.send_muted_message(respond=True)` for
agent prompts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import client, db, provision
from .hmac import HDR_SIG, HDR_TS, verify

log = logging.getLogger("plugin-scheduler.routes")


# 0.6.1: agent_prompt fire turns pass the "all" sentinel and let CORE resolve
# the toolset at turn time. The old registered-name snapshot (_all_tool_names)
# froze whatever happened to be loaded at fire time and bypassed core's
# headless tool gating (luna 074).
_FIRE_TOOLS = "all"


def _retry_delay_s() -> float:
    """Delay before the single re-fire of a dead turn. Long enough for a
    transient core hiccup (restart, DB pool exhaustion) to clear."""
    try:
        return float(os.environ.get("LUNA_FIRE_RETRY_DELAY_S", "60"))
    except ValueError:
        return 60.0


def _result_error(result) -> str | None:
    if isinstance(result, dict) and result.get("error"):
        return str(result["error"])
    return None


def _emit_event_of(body) -> str | None:
    """0.7.0: a fire whose `inputs.emit_event` names a bus event is a
    ZERO-TOKEN wake-up: the receiver emits that event instead of running an
    agent turn. This is what makes a durable low-cost pump possible (luna 075:
    the task-plan watchdog pump) — the subscriber decides whether anything is
    worth a model call. Works with unmodified scheduler-service: the trigger
    is a plain agent_prompt whose `inputs` carry the sentinel."""
    inputs = body.get("inputs")
    ev = inputs.get("emit_event") if isinstance(inputs, dict) else None
    return ev if isinstance(ev, str) and ev.strip() else None


async def _fail_loud(ctx, *, trigger_name, trigger_id, fire_id, err,
                     conversation_id=None) -> None:
    """0.6.1: a fire whose turn AND retry both died must reach the owner —
    silent double-death is the production stall (dead fires looked 'emitted'
    in the log while the agent never moved). Awareness note: visible in the
    conversation, costs no turn. 0.8.0: lands in the same conversation the
    fire itself was routed to."""
    send = getattr(ctx, "send_muted_message", None)
    if send is None:
        return
    streak = 0
    try:
        streak = await db.consecutive_failures(ctx.engine, trigger_id)
    except Exception:  # noqa: BLE001
        pass
    text = (f"Scheduled trigger '{trigger_name}' failed twice — the fire turn "
            f"and its retry both died (fire {fire_id}): {err}.")
    if streak >= 3:
        text += (f" That is {streak} consecutive dead fires for this trigger — "
                 "it looks broken; pause it or fix its target.")
    try:
        await send(f"Scheduled trigger failing: {trigger_name}", text,
                   channel="awareness", respond=False,
                   conversation_id=conversation_id)
    except Exception:  # noqa: BLE001
        log.exception("fire %s: fail-loud note failed", fire_id)


async def _resolve_target(ctx, body: dict) -> str | None:
    """089: explicit fire routing. An agent-created trigger reports into its
    origin conversation while that chat still exists; everything else — owner
    triggers (no meta row), orphaned or stopped origins — lands in the ops
    chat. Returns a conversation id (str) or None (legacy core default).
    Never raises."""
    origin = None
    trigger_id = body.get("trigger_id")
    if trigger_id:
        try:
            meta = await db.get_trigger_meta(ctx.engine, str(trigger_id))
        except Exception:  # noqa: BLE001
            meta = None
        if (meta and meta["created_by"] == "agent"
                and meta["origin_conversation_id"]):
            candidate = meta["origin_conversation_id"]
            try:
                alive = {str(c.id) for c in await ctx.conversations.list()}
                if candidate in alive:
                    origin = candidate
            except Exception:  # noqa: BLE001
                # can't check liveness — trust the recorded origin
                origin = candidate
    if origin is None:
        # 0.5.0 check_back_in stamped the origin into trigger inputs instead
        # of a meta row; such triggers may still exist in the service.
        inputs = body.get("inputs")
        candidate = inputs.get("conversation_id") if isinstance(inputs, dict) else None
        if isinstance(candidate, str) and candidate:
            try:
                alive = {str(c.id) for c in await ctx.conversations.list()}
                if candidate in alive:
                    origin = candidate
            except Exception:  # noqa: BLE001
                origin = candidate
    if origin is not None:
        return origin
    try:
        ops = await ctx.ops_conversation_id()
        return str(ops) if ops else None
    except Exception:  # noqa: BLE001
        log.debug("ops conversation lookup failed", exc_info=True)
        return None


def _origin_scope(body: dict):
    """Bind the billing origin for the whole fire→turn/playbook→derived chain
    (luna-service 048): channel=scheduler + the stable trigger id, so hosted
    usage attributes this and everything it spawns to Scheduled triggers. A
    no-op context manager if luna core predates the SDK helper.

    042/phase09 — creator-declared origin: a plugin that registers triggers as
    a wake mechanism (goal-seek heartbeats) is not "a scheduled reminder"; its
    spend belongs in its own usage bucket. The trigger's ``inputs`` ride the
    fire body verbatim, so the creator may declare
    ``inputs={"billing": {"root_action_type": ..., "job_id": ...}}`` and the
    fire is stamped with that origin instead of the scheduled_run default
    (channel stays "scheduler" — the fire IS scheduler-initiated)."""
    from contextlib import nullcontext
    try:
        from luna_sdk import billing_origin_scope
    except Exception:  # noqa: BLE001 — older core: degrade to no attribution
        return nullcontext()
    root_action_type = "scheduled_run"
    job_id = str(body.get("trigger_id") or body.get("fire_id") or "")
    inputs = body.get("inputs")
    declared = inputs.get("billing") if isinstance(inputs, dict) else None
    if isinstance(declared, dict):
        if isinstance(declared.get("root_action_type"), str) and declared["root_action_type"]:
            root_action_type = declared["root_action_type"]
        if isinstance(declared.get("job_id"), str) and declared["job_id"]:
            job_id = declared["job_id"]
    return billing_origin_scope(
        channel="scheduler",
        root_action_type=root_action_type,
        job_id=job_id,
    )


_UNRESOLVED = object()  # sentinel: caller did not pre-resolve the target


async def _emit_fire(ctx, body: dict, conversation=_UNRESOLVED) -> None:
    """Background: turn a recorded fire into an agent turn or playbook run.

    ``conversation``: the pre-resolved target conversation id (batch flush
    resolves before grouping); left unresolved, the agent_prompt branch
    resolves it itself via :func:`_resolve_target`."""
    fire_id = body["fire_id"]
    if not await db.claim_dispatch(ctx.engine, fire_id):
        return
    action = body.get("action_type")
    target = body.get("target") or ""
    name = body.get("trigger_name") or "scheduled trigger"
    try:
      with _origin_scope(body):
        if action == "playbook":
            try:
                registered = ctx.tool_registry.get("playbook_run")
            except (KeyError, AttributeError):
                await db.set_outcome(ctx.engine, fire_id, "failed: no playbook runner")
                log.warning("fire %s: playbook fire but plugin-playbooks absent", fire_id)
                return
            inputs = body.get("inputs")
            kwargs = {"name": target}
            if inputs:
                kwargs["inputs"] = json.dumps(inputs) if not isinstance(inputs, str) else inputs
            result = await registered.handler(**kwargs)
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {"error": "unparseable playbook result"}
            if not isinstance(result, dict):
                result = {"error": "unstructured playbook result"}
            status = str(result.get("status") or "")
            if status == "timed_out_unknown":
                await db.set_outcome(
                    ctx.engine, fire_id,
                    f"unknown: playbook_run {target} run_id={result.get('run_id', '?')}: "
                    f"{result.get('error') or 'external effect may have committed'}",
                )
            elif result.get("error") or result.get("ok") is False or status in ("failed", "refused", "rejected", "cancelled"):
                await db.set_outcome(ctx.engine, fire_id,
                                     f"failed: playbook_run {target}: {result.get('error') or status}")
            elif status in ("running", "parked"):
                await db.set_outcome(ctx.engine, fire_id,
                                     f"{status}: playbook_run {target} run_id={result.get('run_id', '?')}")
            else:
                await db.set_outcome(ctx.engine, fire_id, f"emitted: playbook_run {target}")
            log.info("fire %s: playbook_run(%s) -> %.200s", fire_id, target, result)
        elif action == "agent_prompt" and _emit_event_of(body) is not None:
            event = _emit_event_of(body)
            events = getattr(ctx, "events", None)
            if events is None:
                await db.set_outcome(ctx.engine, fire_id, "failed: no event bus")
                return
            await events.emit(event, {
                "source": "plugin-scheduler",
                "trigger_id": body.get("trigger_id"),
                "trigger_name": name,
                "fire_id": fire_id,
                "fired_at": body.get("fired_at"),
                "inputs": body.get("inputs"),
            })
            await db.set_outcome(ctx.engine, fire_id, f"emitted: bus_event {event}")
            log.info("fire %s: bus_event %s", fire_id, event)
        elif action == "agent_prompt":
            send = getattr(ctx, "send_muted_message", None)
            if send is None:
                await db.set_outcome(ctx.engine, fire_id, "failed: no muted-message API")
                return
            # The agent has no clock (its system prompt carries no current
            # date/time), so the fire time is the turn's only "now" anchor.
            fired_at = body.get("fired_at")
            when = f" at {fired_at} (UTC)" if fired_at else ""
            content = f"A scheduled trigger just fired{when}. Do this now:\n\n{target}"
            if conversation is _UNRESOLVED:
                conversation = await _resolve_target(ctx, body)
            result = await send(
                f"Scheduled: {name}", content,
                respond=True, tools=_FIRE_TOOLS,
                conversation_id=conversation,
            )
            err = _result_error(result)
            if err:
                # 0.6.1: retry ONCE — most dead turns are transient (core
                # restart, model 5xx). The retry turn says so, so the agent
                # resumes recorded progress instead of redoing the task.
                await db.set_outcome(ctx.engine, fire_id,
                                     f"error: {err} (will retry)")
                await asyncio.sleep(_retry_delay_s())
                result = await send(
                    f"RETRY — Scheduled: {name}",
                    f"RETRY: the first attempt to run this scheduled trigger "
                    f"died ({err}). Pick up from any recorded progress — do "
                    f"not redo completed steps.\n\n{content}",
                    respond=True, tools=_FIRE_TOOLS,
                    conversation_id=conversation,
                )
                rerr = _result_error(result)
                if rerr:
                    await db.set_outcome(ctx.engine, fire_id,
                                         f"error: {rerr} (after retry)")
                    await _fail_loud(ctx, trigger_name=name,
                                     trigger_id=body.get("trigger_id"),
                                     fire_id=fire_id, err=rerr,
                                     conversation_id=conversation)
                elif isinstance(result, dict) and result.get("responded") is False:
                    await db.set_outcome(ctx.engine, fire_id,
                                         "emitted: agent_prompt (retry, no reply)")
                else:
                    await db.set_outcome(ctx.engine, fire_id,
                                         "emitted: agent_prompt (retry)")
            elif isinstance(result, dict) and result.get("responded") is False:
                # turn ran but produced no reply — visible so monitoring can
                # tell it apart from a normal emitted turn
                await db.set_outcome(ctx.engine, fire_id, "emitted: agent_prompt (no reply)")
            else:
                await db.set_outcome(ctx.engine, fire_id, "emitted: agent_prompt")
        else:
            await db.set_outcome(ctx.engine, fire_id, f"failed: unknown action_type {action!r}")
    except Exception as exc:  # noqa: BLE001 — outcome must always land
        log.exception("fire %s: emit failed", fire_id)
        try:
            # type name first: a bare httpx timeout str()s to "" and would
            # otherwise record an empty, unactionable outcome
            await db.set_outcome(ctx.engine, fire_id,
                                 f"error: {type(exc).__name__}: {exc}"[:300])
        except Exception:  # noqa: BLE001
            pass


def _batch_window_s() -> float:
    """070/phase005: co-due agent_prompt fires (same cron minute → several
    near-simultaneous webhooks) collapse into ONE muted message / ONE agent
    turn instead of N. 0 disables batching."""
    try:
        return float(os.environ.get("LUNA_FIRE_BATCH_WINDOW_S", "5"))
    except ValueError:
        return 5.0


class _FireBatch:
    """Collects agent_prompt fire bodies for a short window, then emits them
    as a single combined turn. Playbook fires never batch — they don't cost
    an agent turn. Per-fire db outcomes are preserved."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.pending: list[dict] = []
        self._scheduled = False

    def add(self, body: dict) -> None:
        self.pending.append(body)
        if not self._scheduled:
            self._scheduled = True
            asyncio.get_running_loop().create_task(self._flush_later())

    async def _flush_later(self) -> None:
        await asyncio.sleep(_batch_window_s())
        # no await between these two lines: an add() after the swap starts a
        # fresh flusher, so nothing can strand in self.pending
        self._scheduled = False
        batch, self.pending = self.pending, []
        if not batch:
            return
        # 0.8.0 (089): fires route per-trigger (agent one-shots → origin chat,
        # everything else → ops), so co-due fires only merge into one turn
        # when they land in the SAME conversation.
        groups: dict[str | None, list[dict]] = {}
        for body in batch:
            groups.setdefault(await _resolve_target(self.ctx, body), []).append(body)
        for conversation, bodies in groups.items():
            await _emit_agent_batch(self.ctx, bodies, conversation=conversation)


async def _emit_agent_batch(ctx, bodies: list[dict], conversation=_UNRESOLVED) -> None:
    """One muted message for the whole batch; every fire records an outcome."""
    if len(bodies) == 1:
        # byte-identical single-fire path
        await _emit_fire(ctx, bodies[0], conversation=conversation)
        return
    bodies = [b for b in bodies if await db.claim_dispatch(ctx.engine, b["fire_id"])]
    if not bodies:
        return
    if len(bodies) == 1:
        # Already claimed above; use the batch path without another claim.
        pass
    if conversation is _UNRESOLVED:
        conversation = await _resolve_target(ctx, bodies[0])
    fire_ids = [b["fire_id"] for b in bodies]
    try:
      with _origin_scope(bodies[0]):
        send = getattr(ctx, "send_muted_message", None)
        if send is None:
            for fid in fire_ids:
                await db.set_outcome(ctx.engine, fid, "failed: no muted-message API")
            return
        fired_at = bodies[0].get("fired_at")
        when = f" at {fired_at} (UTC)" if fired_at else ""
        jobs = "\n\n".join(
            f"{i}. [{b.get('trigger_name') or 'scheduled trigger'}] "
            f"{b.get('target') or ''}"
            for i, b in enumerate(bodies, 1)
        )
        content = (f"{len(bodies)} scheduled triggers fired together{when}. "
                   f"Work through all of them in this one turn, in order:\n\n{jobs}")
        result = await send(
            f"Scheduled: {len(bodies)} triggers", content,
            respond=True, tools=_FIRE_TOOLS,
            conversation_id=conversation,
        )
        err = _result_error(result)
        if err:
            # same retry-once contract as the single-fire path
            for fid in fire_ids:
                await db.set_outcome(ctx.engine, fid, f"error: {err} (will retry)")
            await asyncio.sleep(_retry_delay_s())
            result = await send(
                f"RETRY — Scheduled: {len(bodies)} triggers",
                f"RETRY: the first attempt to run these scheduled triggers "
                f"died ({err}). Pick up from any recorded progress — do not "
                f"redo completed steps.\n\n{content}",
                respond=True, tools=_FIRE_TOOLS,
                conversation_id=conversation,
            )
            rerr = _result_error(result)
            if rerr:
                for fid in fire_ids:
                    await db.set_outcome(ctx.engine, fid, f"error: {rerr} (after retry)")
                await _fail_loud(ctx, trigger_name=bodies[0].get("trigger_name")
                                 or "scheduled trigger",
                                 trigger_id=bodies[0].get("trigger_id"),
                                 fire_id=fire_ids[0], err=rerr,
                                 conversation_id=conversation)
                return
            if isinstance(result, dict) and result.get("responded") is False:
                outcome = f"emitted: agent_prompt (batch of {len(bodies)}, retry, no reply)"
            else:
                outcome = f"emitted: agent_prompt (batch of {len(bodies)}, retry)"
        elif isinstance(result, dict) and result.get("responded") is False:
            outcome = f"emitted: agent_prompt (batch of {len(bodies)}, no reply)"
        else:
            outcome = f"emitted: agent_prompt (batch of {len(bodies)})"
        for fid in fire_ids:
            await db.set_outcome(ctx.engine, fid, outcome)
    except Exception as exc:  # noqa: BLE001 — outcome must always land
        log.exception("fire batch %s: emit failed", fire_ids)
        for fid in fire_ids:
            try:
                await db.set_outcome(ctx.engine, fid,
                                     f"error: {type(exc).__name__}: {exc}"[:300])
            except Exception:  # noqa: BLE001
                pass


def register_routes(app, ctx):
    router = APIRouter(prefix="/api/p/plugin-scheduler", tags=["scheduler"])
    batch = _FireBatch(ctx)

    # ---------------- fire ingress ----------------
    @router.post("/fire")
    async def fire(request: Request):
        raw = (await request.body()).decode("utf-8")
        cfg = await client.get_config(ctx)
        if cfg is None:
            raise HTTPException(503, "scheduler not configured")
        if not verify(cfg["secret"], raw, request.headers.get(HDR_TS),
                      request.headers.get(HDR_SIG)):
            raise HTTPException(403, "bad signature")
        try:
            body = json.loads(raw)
            fire_id = str(body["fire_id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            raise HTTPException(400, "invalid fire payload")

        inserted = await db.record_fire(
            ctx.engine, fire_id=fire_id,
            trigger_id=body.get("trigger_id"),
            trigger_name=body.get("trigger_name"),
            action_type=body.get("action_type") or "unknown",
            target=body.get("target"), outcome="received", payload_json=raw,
        )
        if not inserted:
            # The prior in-memory dispatch may have vanished after its DB
            # receipt. A conditional claim in _emit_fire prevents two turns.
            if await db.outcome_for(ctx.engine, fire_id) == "received":
                asyncio.get_running_loop().create_task(_emit_fire(ctx, body))
            return {"ok": True, "deduped": True}
        if (body.get("action_type") == "agent_prompt" and _batch_window_s() > 0
                and _emit_event_of(body) is None):
            batch.add(body)  # co-due fires merge into one agent turn
        else:
            asyncio.get_running_loop().create_task(_emit_fire(ctx, body))
        return {"ok": True, "deduped": False}

    # ---------------- settings-tab API (signing stays server-side) ----------
    @router.get("/status")
    async def status():
        cfg = await client.get_config(ctx)
        if cfg is None:
            return {"connected": False, "state": "manual",
                    "detail": "requires a hosted Luna or a self-run scheduler-service"}
        try:
            await client.list_triggers(ctx)
        except Exception as exc:  # noqa: BLE001
            return {"connected": False, "state": "reconnect",
                    "service_url": cfg["service_url"], "account_id": cfg["account_id"],
                    "detail": str(exc)}
        return {"connected": True, "state": "connected",
                "service_url": cfg["service_url"], "account_id": cfg["account_id"]}

    @router.post("/connect")
    async def connect():
        result = await provision.ensure_connected(ctx)
        if result.get("state") == "connected":
            # Vault creds exist but may be stale — validate with a real call;
            # client._request self-repairs (rotate-connect) on 401/403.
            try:
                await client.list_triggers(ctx)
            except Exception as exc:  # noqa: BLE001
                return {"state": "error", "detail": str(exc)}
        return result

    @router.post("/manual-config")
    async def manual_config(request: Request):
        body = await request.json()
        for f in ("service_url", "account_id", "secret"):
            if not body.get(f):
                raise HTTPException(422, f"missing {f}")
        await provision.store_config(ctx, body["service_url"], body["account_id"],
                                     body["secret"])
        return await status()

    def _proxy(coro):
        async def run():
            try:
                return await coro
            except client.SchedulerError as exc:
                raise HTTPException(exc.status, exc.detail)
            except client.NotConnected as exc:
                raise HTTPException(503, str(exc))
        return run()

    @router.get("/triggers")
    async def triggers_list():
        return {"triggers": await _proxy(client.list_triggers(ctx))}

    @router.post("/triggers")
    async def triggers_create(request: Request):
        b = await request.json()
        return await _proxy(client.create_trigger(
            ctx, name=b.get("name", ""), expr=b.get("expr", ""),
            action_type=b.get("action_type", ""), target=b.get("target", ""),
            inputs=b.get("inputs"), timezone=b.get("timezone"),
            max_runs=b.get("max_runs")))

    @router.post("/parse")
    async def parse(request: Request):
        b = await request.json()
        return await _proxy(client.parse_preview(ctx, b.get("expr", ""), b.get("timezone")))

    @router.post("/triggers/{trigger_id}/pause")
    async def t_pause(trigger_id: str):
        return await _proxy(client.pause_trigger(ctx, trigger_id))

    @router.post("/triggers/{trigger_id}/resume")
    async def t_resume(trigger_id: str):
        return await _proxy(client.resume_trigger(ctx, trigger_id))

    @router.post("/triggers/{trigger_id}/run-now")
    async def t_run_now(trigger_id: str):
        return await _proxy(client.run_now(ctx, trigger_id))

    @router.delete("/triggers/{trigger_id}")
    async def t_delete(trigger_id: str):
        return await _proxy(client.delete_trigger(ctx, trigger_id))

    @router.get("/fires")
    async def fires():
        # service history is source of truth; local table is the fallback and
        # carries the plugin-side outcome (emitted/deduped/failed)
        local = await db.recent_fires(ctx.engine, limit=50)
        try:
            remote = await client.list_fires(ctx, limit=50)
        except Exception:  # noqa: BLE001
            remote = []
        return {"local": local, "service": remote}

    @router.get("/ui/settings/", response_class=HTMLResponse)
    async def settings_ui():
        return HTMLResponse(_SETTINGS_HTML)

    app.include_router(router)


_SETTINGS_HTML = """<!doctype html>
<meta charset="utf-8">
<style>
  :root{--bg:#111318;--card:#191c23;--line:#2a2d36;--fg:#e6e6e6;--muted:#9aa0ae;
        --ok:#8fe3a4;--okbg:#12351c;--bad:#f0a0a0;--badbg:#3a1a1a;
        --accent:#7aa2f7;--warn:#e0b050}
  *{box-sizing:border-box}
  body{font:14px/1.45 -apple-system,system-ui,sans-serif;margin:0;padding:20px;
       color:var(--fg);background:var(--bg)}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
     margin:22px 0 10px;font-weight:600}
  .strip{padding:10px 14px;border-radius:8px;margin-bottom:14px;display:flex;
         align-items:center;gap:8px}
  .ok{background:var(--okbg);color:var(--ok)}.bad{background:var(--badbg);color:var(--bad)}
  .muted{color:var(--muted)}.small{font-size:12px}
  .spin{width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;
        border-radius:50%;display:inline-block;animation:spin .7s linear infinite;vertical-align:-1px}
  @keyframes spin{to{transform:rotate(360deg)}}
  .loading{color:var(--muted);font-size:12px;padding:8px 2px}
  input,select{background:#1a1d24;color:var(--fg);border:1px solid var(--line);
               border-radius:6px;padding:6px 8px;margin:2px 4px 2px 0}
  button{background:#2a2d36;color:var(--fg);border:0;border-radius:6px;
         padding:5px 11px;cursor:pointer;margin-right:4px;font-size:13px}
  button:hover{background:#3a3e4a}button:disabled{opacity:.5;cursor:default}
  .form{background:var(--card);padding:12px;border-radius:10px;margin:10px 0}
  #preview{margin-left:8px}
  #cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(258px,1fr));gap:10px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;
        padding:12px 13px;display:flex;flex-direction:column;gap:6px}
  .card.off{opacity:.6}
  .chead{display:flex;justify-content:space-between;align-items:center;gap:8px}
  .cname{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .badge{font-size:11px;padding:2px 8px;border-radius:999px;white-space:nowrap}
  .b-on{background:var(--okbg);color:var(--ok)}.b-off{background:#33261a;color:var(--warn)}
  .b-done{background:#26314a;color:var(--accent)}
  .sched{font-size:13px}.sched .cron{color:var(--muted);font-size:11px}
  .snip{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;
        display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
  .meta{font-size:11px;color:var(--muted);display:flex;flex-wrap:wrap;gap:3px 10px}
  .cfoot{display:flex;gap:4px;margin-top:2px}
  .cfoot button{padding:3px 9px;font-size:12px}
  .empty{color:var(--muted);font-size:13px;padding:10px 2px}
  .fire{border-bottom:1px solid var(--line)}
  .frow{display:flex;gap:10px;align-items:center;padding:7px 2px;cursor:pointer;font-size:13px}
  .frow:hover{background:#161922}
  .fdot{width:7px;height:7px;border-radius:50%;flex:none}
  .fx-delivered,.fx-emitted{background:var(--ok)}.fx-failed,.fx-dead{background:var(--bad)}
  .fx-pending{background:var(--warn)}.fx-other{background:var(--muted)}
  .ftime{min-width:118px}.fname{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .fstatus{color:var(--muted);font-size:12px}.caret{color:var(--muted);font-size:10px;width:10px}
  .fdetail{display:none;padding:0 2px 9px 27px;color:var(--muted);font-size:12px}
  .fdetail.open{display:block}.fdetail div{margin:2px 0}.fdetail b{color:var(--fg);font-weight:500}
</style>
<div id="strip" class="strip muted"><span class="spin"></span> checking…</div>
<div id="manual" style="display:none" class="form">
  <div class="muted small">Requires a hosted Luna or a self-run scheduler-service. Manual config:</div>
  <input id="m_url" placeholder="service url (http://…)" size="26">
  <input id="m_acct" placeholder="account id" size="14">
  <input id="m_secret" placeholder="secret" size="22" type="password">
  <button onclick="saveManual(this)">Save</button>
  <button onclick="connect(this)">Connect (hosted)</button>
</div>

<h2>New trigger</h2>
<div class="form">
  <input id="f_name" placeholder="name" size="12">
  <input id="f_expr" placeholder="every weekday at 09:00" size="22" oninput="preview()">
  <span id="preview" class="muted small"></span><br>
  <select id="f_action"><option value="agent_prompt">agent prompt</option>
  <option value="playbook">playbook</option></select>
  <input id="f_target" placeholder="prompt text / playbook name" size="30">
  <input id="f_inputs" placeholder='inputs JSON (playbook)' size="16" style="display:none">
  <input id="f_tz" placeholder="timezone (optional)" size="13">
  <input id="f_runs" placeholder="runs (∞)" size="6"
         title="how many times to fire — blank = forever, 1 = once, 0 = never">
  <button onclick="createTrigger(this)">Add trigger</button>
  <span id="f_err" class="small" style="color:#f0a0a0"></span>
</div>

<h2>Triggers</h2>
<div id="cards"><div class="loading"><span class="spin"></span> loading…</div></div>

<h2>Recent fires</h2>
<div id="fires"><div class="loading"><span class="spin"></span> loading…</div></div>

<script>
// Served at `<base>/api/p/plugin-scheduler/ui/settings/`; on hosted Luna the base
// carries an `/a/<slug>` prefix, so API calls MUST be relative (up two segments)
// to stay agent-scoped — an absolute `/api/p/...` path drops the prefix and 404s.
const api = p => `../..${p}`;
const esc = s => (s ?? '').toString().replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = t => t ? new Date(t).toLocaleString() : '—';
function rel(t){
  if(!t) return '';
  const diff = new Date(t).getTime() - Date.now();
  const s = Math.round(Math.abs(diff)/1000);
  if(s < 45) return 'just now';
  let n, u;
  if(s < 3600){n = Math.round(s/60); u='min';}
  else if(s < 86400){n = Math.round(s/3600); u='hr';}
  else {n = Math.round(s/86400); u='day';}
  const label = `${n} ${u}${n>1?'s':''}`;
  return diff < 0 ? `${label} ago` : `in ${label}`;
}
async function j(method, path, body){
  const r = await fetch(api(path), {method,
    headers:{'content-type':'application/json'},
    body: body ? JSON.stringify(body) : undefined});
  const d = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(d.detail || r.status);
  return d;
}
// run fn with the button showing a spinner + disabled
async function busy(btn, fn){
  let old;
  if(btn){old = btn.innerHTML; btn.disabled = true; btn.innerHTML = '<span class="spin"></span>';}
  try{ return await fn(); }
  finally{ if(btn){btn.disabled = false; btn.innerHTML = old;} }
}
async function refreshStatus(){
  const el = document.getElementById('strip');
  el.className = 'strip muted'; el.innerHTML = '<span class="spin"></span> checking…';
  const s = await j('GET','/status').catch(e=>({connected:false,detail:e.message}));
  el.className = 'strip ' + (s.connected ? 'ok' : 'bad');
  el.textContent = s.connected
    ? `Connected — ${s.account_id} @ ${s.service_url}`
    : (s.state === 'reconnect' ? `Reconnect needed — ${s.detail||''}`
                              : `Not connected — ${s.detail||''}`);
  document.getElementById('manual').style.display = s.connected ? 'none' : 'block';
  return s.connected;
}
function runsMeta(t){
  if(t.max_runs === 0) return 'never runs';
  if(t.max_runs != null) return `${t.runs_done||0}/${t.max_runs} runs`;
  if(t.runs_done) return `${t.runs_done} runs`;
  return '';
}
function stateBadge(t){
  const exhausted = t.max_runs != null && (t.runs_done||0) >= t.max_runs && t.max_runs > 0;
  if(t.enabled) return '<span class="badge b-on">on</span>';
  if(exhausted) return `<span class="badge b-done">done ${t.runs_done}/${t.max_runs}</span>`;
  return '<span class="badge b-off">paused</span>';
}
function card(t){
  const meta = [
    t.next_run_at ? `next ${rel(t.next_run_at)}` : '',
    t.last_run_at ? `ran ${rel(t.last_run_at)}` : 'never run',
    runsMeta(t),
    esc(t.timezone||'UTC'),
  ].filter(Boolean).map(x=>`<span>${x}</span>`).join('');
  const exhausted = t.max_runs != null && (t.runs_done||0) >= t.max_runs && t.max_runs > 0;
  const foot = [
    (!exhausted) ? `<button onclick="act(this,'${t.id}','${t.enabled?'pause':'resume'}')">${t.enabled?'pause':'resume'}</button>` : '',
    `<button onclick="act(this,'${t.id}','run-now')">run now</button>`,
    `<button onclick="del(this,'${t.id}')">delete</button>`,
  ].join('');
  return `<div class="card ${t.enabled?'':'off'}">
    <div class="chead"><span class="cname" title="${esc(t.name)}">${esc(t.name)}</span>${stateBadge(t)}</div>
    <div class="sched">${esc(t.expr_raw)} <span class="cron">${esc(t.expr_cron)}</span></div>
    <div class="snip">${esc(t.action_type)}: ${esc(t.target)}</div>
    <div class="meta">${meta}</div>
    <div class="cfoot">${foot}</div>
  </div>`;
}
async function refreshTriggers(){
  const box = document.getElementById('cards');
  const d = await j('GET','/triggers').catch(()=>({triggers:[]}));
  // 075/phase7: plugin-tasks plumbing (the zero-token pump + task wakes) is
  // not the owner's schedule — the task card is its surface. Hide it here.
  const ts = (d.triggers || []).filter(t =>
    t.created_by !== 'plugin-tasks' &&
    t.name !== 'luna-task-pump' && !(t.name||'').startsWith('task-wake-'));
  box.innerHTML = ts.length ? ts.map(card).join('')
    : '<div class="empty">No triggers yet. Add one above, or ask the agent.</div>';
}
function fireClass(status, outcome){
  const v = (status||outcome||'').toLowerCase();
  if(v.includes('deliver')||v.includes('emit')) return 'fx-delivered';
  if(v.includes('fail')||v.includes('error')) return 'fx-failed';
  if(v.includes('dead')) return 'fx-dead';
  if(v.includes('pend')) return 'fx-pending';
  return 'fx-other';
}
async function refreshFires(){
  const box = document.getElementById('fires');
  const d = await j('GET','/fires').catch(()=>({local:[],service:[]}));
  const byId = Object.fromEntries((d.local||[]).map(f=>[f.fire_id,f]));
  const rows = (d.service && d.service.length) ? d.service.map(f=>({
      t:f.created_at, name:(byId[f.fire_id]||{}).trigger_name||f.trigger_id.slice(0,8),
      action:(byId[f.fire_id]||{}).action_type||'',
      svc:f.status + (f.last_error?` (${f.last_error})`:''),
      status:f.status, out:(byId[f.fire_id]||{}).outcome||'—',
      resp:f.response_status, attempts:f.attempts}))
    : (d.local||[]).map(f=>({t:f.received_at,name:f.trigger_name,action:f.action_type,
      svc:'—',status:f.outcome,out:f.outcome}));
  if(!rows.length){box.innerHTML='<div class="empty">No fires yet.</div>';return;}
  // 075/phase7: a bus-event fire is a zero-token wake (task pump / task
  // wake), not a failed prompt — label it as what it is.
  const label = r => (r.out||'').startsWith('emitted: bus_event')
    ? 'wake (zero-token)' : (r.status||'');
  box.innerHTML = rows.slice(0,30).map((r,i)=>`
    <div class="fire">
      <div class="frow" onclick="document.getElementById('fd${i}').classList.toggle('open')">
        <span class="fdot ${fireClass(r.status,r.out)}"></span>
        <span class="ftime">${esc(rel(r.t)||fmt(r.t))}</span>
        <span class="fname">${esc(r.name)}</span>
        <span class="fstatus">${esc(label(r))}</span>
        <span class="caret">▸</span>
      </div>
      <div id="fd${i}" class="fdetail">
        <div><b>when</b> ${esc(fmt(r.t))}</div>
        ${r.action?`<div><b>action</b> ${esc(r.action)}</div>`:''}
        <div><b>service status</b> ${esc(r.svc)}</div>
        <div><b>plugin outcome</b> ${esc(r.out)}</div>
        ${r.resp!=null?`<div><b>response</b> HTTP ${esc(r.resp)}</div>`:''}
        ${r.attempts!=null?`<div><b>attempts</b> ${esc(r.attempts)}</div>`:''}
      </div>
    </div>`).join('');
}
let pvTimer;
function preview(){
  clearTimeout(pvTimer);
  const expr = document.getElementById('f_expr').value.trim();
  const out = document.getElementById('preview');
  if(!expr){out.textContent='';return}
  out.textContent = 'checking…'; out.style.color='#9aa0ae';
  pvTimer = setTimeout(async ()=>{
    try{const d = await j('POST','/parse',{expr, timezone:document.getElementById('f_tz').value||undefined});
        out.textContent = `→ ${d.expr_cron}, next ${fmt(d.next_run_at)}`; out.style.color='#8fe3a4';}
    catch(e){out.textContent = e.message; out.style.color='#f0a0a0';}
  }, 400);
}
document.getElementById('f_action').onchange = e =>
  document.getElementById('f_inputs').style.display =
    e.target.value === 'playbook' ? 'inline-block' : 'none';
async function createTrigger(btn){
  const err = document.getElementById('f_err'); err.textContent='';
  let inputs;
  const rawInputs = document.getElementById('f_inputs').value.trim();
  if(rawInputs){try{inputs = JSON.parse(rawInputs)}catch{err.textContent='inputs is not valid JSON';return}}
  const rawRuns = document.getElementById('f_runs').value.trim();
  let max_runs;
  if(rawRuns !== ''){
    max_runs = parseInt(rawRuns, 10);
    if(!Number.isInteger(max_runs) || max_runs < 0){err.textContent='runs must be a whole number ≥ 0';return}
  }
  try{
    await busy(btn, ()=> j('POST','/triggers',{name:document.getElementById('f_name').value,
      expr:document.getElementById('f_expr').value,
      action_type:document.getElementById('f_action').value,
      target:document.getElementById('f_target').value,
      timezone:document.getElementById('f_tz').value||undefined, inputs, max_runs}));
    ['f_name','f_expr','f_target','f_inputs','f_runs'].forEach(i=>document.getElementById(i).value='');
    document.getElementById('preview').textContent='';
    refreshTriggers();
  }catch(e){err.textContent = e.message}
}
async function act(btn, id, verb){
  try{ await busy(btn, ()=> j('POST',`/triggers/${id}/${verb}`)); }
  catch(e){ alert(e.message); }
  refreshTriggers(); refreshFires();
}
async function del(btn, id){
  if(!confirm('Delete this trigger?')) return;
  try{ await busy(btn, ()=> j('DELETE',`/triggers/${id}`)); }
  catch(e){ alert(e.message); }
  refreshTriggers();
}
async function saveManual(btn){
  try{await busy(btn, ()=> j('POST','/manual-config',{service_url:document.getElementById('m_url').value,
    account_id:document.getElementById('m_acct').value,
    secret:document.getElementById('m_secret').value})); refreshAll();}
  catch(e){alert(e.message)}
}
async function connect(btn){
  try{ await busy(btn, ()=> j('POST','/connect')); refreshAll(); }
  catch(e){ alert(e.message); }
}
async function refreshAll(){if(await refreshStatus()){refreshTriggers();refreshFires();}}
refreshAll(); setInterval(refreshAll, 30000);
</script>
"""
