"""plugin-scheduler — time-based triggers for Luna.

The clock lives in the external scheduler-service (always on, survives this
Luna sleeping); this plugin is the UX, the registration client, and the fire
receiver. Triggers run either an agent prompt (full agent loop, approvals,
tools) or a playbook by name. Imports ``luna_sdk`` only — never ``luna.*``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SettingsTab, ToolDef

from . import client, db, provision

log = logging.getLogger("plugin-scheduler")

TOOL_NAMES = ["trigger_create", "trigger_list", "trigger_update", "trigger_delete",
              "trigger_pause", "trigger_resume", "trigger_run_now", "remind_me"]

# 089: build/operate modes. Reading the schedule is safe everywhere; anything
# that changes the schedule (or fires it) is build-surface work — allowed only
# where the owner is building or has approved a fix for publishing. Absent
# from planning (plan first), identify (diagnose first) and fix_approve
# (publishing a fix there happens via the owner's approval card, not tools).
_READ_MODES = ["planning", "building", "identify", "fix_approve", "fix_publish"]
_WRITE_MODES = ["building", "fix_publish"]

_CAPABILITY_NOTE = (
    "Scheduler: you can schedule work on a clock with the trigger_* tools. A "
    "trigger runs either an agent prompt (as if the owner typed it — full agent "
    "loop with your tools and normal approvals) or a playbook by name. The clock "
    "lives in an external always-on service: triggers survive restarts and fire "
    "even while this Luna is asleep, so schedule confidently. Expressions may be "
    "5-field cron or plain phrases like 'every weekday at 09:00' (minimum "
    "interval one minute). Playbook steps may also call these tools, so playbooks "
    "can schedule themselves and each other. For a quick one-shot, remind_me("
    "minutes, note) hands the note back to you as a prompt when it fires."
)


def _err(exc: Exception) -> dict[str, Any]:
    return {"error": str(exc)}


async def _playbook_fire_readiness(ctx: PluginContext, target: str) -> dict[str, str]:
    """Tell the creator whether a direct fire can enter a live playbook."""
    try:
        overview_tool = ctx.tool_registry.get("playbook_overview")
        raw = await overview_tool.handler(name=target)
        overview = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001 — creation succeeded; readiness is advisory
        return {"status": "unverified", "reason": "Playbook state could not be checked; inspect it before calling the schedule ready."}
    if not isinstance(overview, dict) or overview.get("error"):
        return {"status": "blocked", "reason": "The target playbook was not found; a direct fire cannot run it."}
    if (overview.get("playbook_run_executes") or {}).get("version") is None:
        return {"status": "blocked", "reason": "The playbook has no live version; publish a tested version before a direct fire."}
    mode = overview.get("autonomy")
    if mode == "agent_must_confirm":
        return {
            "status": "parks_for_owner",
            "reason": "Each scheduled fire will park on a per-run owner card while the owner is away.",
            "next": "Only if the owner explicitly authorized unattended execution, call playbook_set_autonomy with agent_may_trigger through its approval gate; then verify the stored mode and a real fire.",
        }
    if mode == "manual_only":
        return {"status": "blocked", "reason": "The playbook is manual_only; direct scheduled runs are refused."}
    if mode == "agent_may_trigger":
        return {"status": "admission_ready", "reason": "The live playbook admits direct fires; test a real fire and its persisted result before claiming execution works."}
    return {"status": "unverified", "reason": "Unknown playbook autonomy; inspect it before calling the schedule ready."}


class SchedulerPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-scheduler",
        version="0.8.3",
        description="Time-based triggers: run prompts or playbooks on a schedule, even while Luna sleeps.",
        category="system",
        shown_name="Scheduler",
        icon="clock",
        depends_on=["plugin-vault"],
        routes_module="routes",
        license="MIT",
        settings_tabs=[
            SettingsTab(
                id="scheduler",
                label="Scheduler",
                icon="clock",
                sort_order=67,
                iframe_src="/api/p/plugin-scheduler/ui/settings/",
            ),
        ],
    )

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._unsub_stopped = None

    async def on_load(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        await db.create_tables(ctx.engine)
        self._register_tools(ctx)
        # 089: an agent-created one-shot dies with its origin chat — cancel on
        # chat stop; a startup sweep (in the provision task) reaps orphans
        # whose origin was deleted while this Luna slept.
        self._unsub_stopped = ctx.events.subscribe(
            "chat.stopped", self._on_chat_stopped, background=True)
        self._schedule_provision(ctx)
        self._schedule_pending_recovery(ctx)
        log.info("plugin-scheduler loaded (tools=%d)", len(TOOL_NAMES))

    async def on_unload(self, ctx: PluginContext) -> None:
        if self._unsub_stopped is not None:
            self._unsub_stopped()
            self._unsub_stopped = None

    async def prompt_sections(self) -> list[str]:
        return [_CAPABILITY_NOTE]

    async def _on_chat_stopped(self, payload: dict) -> None:
        conv_id = payload.get("conversation_id")
        ctx = self._ctx
        if not conv_id or ctx is None:
            return
        await self._cancel_one_shots(ctx, str(conv_id), reason="chat stopped")

    @staticmethod
    async def _cancel_one_shots(ctx: PluginContext, origin: str, *, reason: str) -> None:
        try:
            metas = await db.agent_one_shots(ctx.engine, origin)
        except Exception:  # noqa: BLE001
            log.debug("one-shot lookup failed", exc_info=True)
            return
        for meta in metas:
            tid = meta["trigger_id"]
            try:
                await client.delete_trigger(ctx, tid)
                log.info("cancelled agent one-shot %s (%s)", tid, reason)
            except Exception:  # noqa: BLE001
                # Already gone server-side, or the service is unreachable;
                # either way the meta row must not resurrect the sweep forever.
                log.warning("cancel of one-shot %s failed (%s)", tid, reason)
            try:
                await db.delete_trigger_meta(ctx.engine, tid)
            except Exception:  # noqa: BLE001
                log.debug("meta delete failed for %s", tid, exc_info=True)

    async def _sweep_orphan_one_shots(self, ctx: PluginContext) -> None:
        """Startup sweep: agent one-shots whose origin conversation no longer
        exists are cancelled. There is no conversation.deleted bus event, so
        deletions that happened while this Luna was down are only caught here."""
        try:
            metas = await db.agent_one_shots(ctx.engine)
            if not metas:
                return
            alive = {str(c.id) for c in await ctx.conversations.list()}
        except Exception:  # noqa: BLE001
            log.debug("orphan sweep skipped", exc_info=True)
            return
        origins = {m["origin_conversation_id"] for m in metas
                   if m["origin_conversation_id"]}
        for origin in origins - alive:
            await self._cancel_one_shots(ctx, origin, reason="origin conversation gone")

    def _schedule_provision(self, ctx: PluginContext) -> None:
        """Best-effort silent self-provision on load; never blocks or breaks boot."""
        async def _run() -> None:
            try:
                result = await provision.ensure_connected(ctx)
                log.info("scheduler provision on load: %s", result.get("state"))
                if result.get("state") == "connected":
                    # Stored creds may be stale (account deleted / secret
                    # rotated server-side). One real call validates them —
                    # client._request self-repairs on 401/403.
                    await client.list_triggers(ctx)
            except Exception:  # noqa: BLE001
                log.debug("scheduler provision failed", exc_info=True)
            await self._sweep_orphan_one_shots(ctx)

        try:
            asyncio.get_running_loop().create_task(_run())  # noqa: RUF006
        except RuntimeError:
            pass

    def _schedule_pending_recovery(self, ctx: PluginContext) -> None:
        """Replay receipts accepted before a queued dispatch could start."""
        async def _run() -> None:
            from . import routes
            import json

            await asyncio.sleep(1)
            try:
                for raw in await db.pending_fires(ctx.engine):
                    await routes._emit_fire(ctx, json.loads(raw))
            except Exception:  # noqa: BLE001
                log.exception("scheduler pending-fire recovery failed")

        try:
            asyncio.get_running_loop().create_task(_run())
        except RuntimeError:
            pass

    def _register_tools(self, ctx: PluginContext) -> None:
        plugin = self.manifest.name

        def _reg(tool_def: ToolDef, handler) -> None:
            ctx.tool_registry.register(plugin, tool_def, handler)

        async def _create(name: str, schedule_expr: str, action_type: str,
                          target: str, inputs: dict | None = None,
                          timezone: str | None = None,
                          max_runs: int | None = None,
                          unique_name: bool = False,
                          purpose: str | None = None,
                          created_by: str = "plugin-scheduler",
                          plan_id: str | None = None) -> dict[str, Any]:
            # created_by/plan_id are not in the tool schema — programmatic
            # callers (other plugins invoking this handler) pass their own.
            try:
                t = await client.create_trigger(
                    ctx, name=name, expr=schedule_expr, action_type=action_type,
                    target=target, inputs=inputs, timezone=timezone,
                    max_runs=max_runs, unique_name=unique_name, purpose=purpose,
                    created_by=created_by)
            except client.SchedulerError as exc:
                # 422 message returned verbatim so the agent can rephrase
                return {"error": exc.detail}
            except Exception as exc:  # noqa: BLE001
                return _err(exc)
            # 089 provenance: created inside a turn ⇒ agent-created, stamped
            # with its origin chat so fires report there and one-shots die
            # with it. No current turn ⇒ owner/programmatic ⇒ durable.
            origin = ctx.current_conversation_id
            try:
                await db.record_trigger_meta(
                    ctx.engine, trigger_id=str(t["id"]),
                    created_by="agent" if origin is not None else "owner",
                    origin_conversation_id=str(origin) if origin else None,
                    plan_id=plan_id, one_shot=max_runs == 1)
            except Exception:  # noqa: BLE001
                log.warning("trigger meta stamp failed for %s", t["id"], exc_info=True)
            out = {"id": t["id"], "expr_cron": t["expr_cron"],
                   "next_run_at": t["next_run_at"],
                   "max_runs": t.get("max_runs"), "runs_done": t.get("runs_done")}
            if "created" in t:
                # service >= 0.3.0 says whether unique_name updated in place
                out["created"] = t["created"]
                if t.get("updated"):
                    out["updated"] = True
            if action_type == "playbook":
                out["playbook_fire_readiness"] = await _playbook_fire_readiness(ctx, target)
            return out

        _reg(
            ToolDef(
                name="trigger_create",
                description=(
                    "Create a scheduled trigger. It survives restarts and fires even "
                    "while this Luna is asleep (external always-on clock). "
                    "schedule_expr: 5-field cron or a phrase like 'every weekday at "
                    "09:00' / 'every 2 hours' (min interval 1 minute). action_type "
                    "'agent_prompt' runs `target` as if the owner typed it; "
                    "'playbook' runs the playbook named `target` with `inputs`. "
                    "Pass the owner's IANA timezone for wall-clock times. "
                    "max_runs bounds how many times it fires then auto-disables: "
                    "omit for unlimited, 1 for a one-shot reminder (fires once), "
                    "N for exactly N times. unique_name=true makes the name an "
                    "upsert key: creating the same name again updates that "
                    "trigger in place instead of adding a duplicate — use it for "
                    "singleton schedules (a daily digest, a heartbeat)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Short human label."},
                        "schedule_expr": {"type": "string"},
                        "action_type": {"type": "string", "enum": ["agent_prompt", "playbook"]},
                        "target": {"type": "string", "description": "Prompt text or playbook name."},
                        "inputs": {"type": "object", "description": "Playbook inputs (playbook only)."},
                        "timezone": {"type": "string", "description": "IANA tz, e.g. Asia/Jerusalem."},
                        "max_runs": {"type": "integer", "minimum": 0,
                                     "description": "Fire this many times then stop. "
                                     "Omit = forever; 1 = one-shot reminder."},
                        "unique_name": {"type": "boolean", "default": False,
                                        "description": "Upsert by name: same name updates "
                                        "in place, never duplicates."},
                        "purpose": {"type": "string",
                                    "description": "One-line label of what this trigger "
                                    "is for (shown in listings)."},
                    },
                    "required": ["name", "schedule_expr", "action_type", "target"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _create,
        )

        async def _list(enabled_only: bool = False) -> dict[str, Any]:
            try:
                triggers = await client.list_triggers(ctx)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)
            if enabled_only:
                triggers = [t for t in triggers if t.get("enabled")]
            return {"triggers": triggers}

        _reg(
            ToolDef(
                name="trigger_list",
                description=(
                    "List scheduled triggers with schedule, next run, last run and "
                    "enabled state (from the scheduler service — source of truth)."
                ),
                parameters={
                    "type": "object",
                    "properties": {"enabled_only": {"type": "boolean", "default": False}},
                },
                modes=_READ_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _list,
        )

        async def _update(id: str, name: str | None = None,
                          schedule_expr: str | None = None, target: str | None = None,
                          inputs: dict | None = None,
                          timezone: str | None = None) -> dict[str, Any]:
            try:
                t = await client.update_trigger(
                    ctx, id, name=name, expr=schedule_expr, target=target,
                    inputs=inputs, timezone=timezone)
            except client.SchedulerError as exc:
                return {"error": exc.detail}
            except Exception as exc:  # noqa: BLE001
                return _err(exc)
            return {"id": t["id"], "expr_cron": t["expr_cron"],
                    "next_run_at": t["next_run_at"]}

        _reg(
            ToolDef(
                name="trigger_update",
                description=(
                    "Update an existing trigger in place (schedule, target prompt, "
                    "name, inputs or timezone) — keeps its identity and fire "
                    "history, unlike delete+recreate. Omitted fields are unchanged."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "schedule_expr": {"type": "string"},
                        "target": {"type": "string", "description": "New prompt text or playbook name."},
                        "inputs": {"type": "object"},
                        "timezone": {"type": "string"},
                    },
                    "required": ["id"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _update,
        )

        async def _delete(id: str) -> dict[str, Any]:
            try:
                out = await client.delete_trigger(ctx, id)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)
            try:
                await db.delete_trigger_meta(ctx.engine, str(id))
            except Exception:  # noqa: BLE001
                log.debug("meta delete failed for %s", id, exc_info=True)
            return out

        _reg(
            ToolDef(
                name="trigger_delete",
                description="Permanently delete a scheduled trigger (and its fire history).",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                modes=_WRITE_MODES,
                policy="prompt_always",
                risk_level="medium",
            ),
            _delete,
        )

        async def _pause(id: str) -> dict[str, Any]:
            try:
                return await client.pause_trigger(ctx, id)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)

        _reg(
            ToolDef(
                name="trigger_pause",
                description="Pause a trigger: no fires until resumed (pending fires are cancelled).",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _pause,
        )

        async def _resume(id: str) -> dict[str, Any]:
            try:
                return await client.resume_trigger(ctx, id)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)

        _reg(
            ToolDef(
                name="trigger_resume",
                description="Resume a paused trigger (next run recomputed from now — no catch-up).",
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _resume,
        )

        async def _run_now(id: str) -> dict[str, Any]:
            try:
                return await client.run_now(ctx, id)
            except Exception as exc:  # noqa: BLE001
                return _err(exc)

        _reg(
            ToolDef(
                name="trigger_run_now",
                description=(
                    "Fire a trigger immediately (out of schedule). The resulting run "
                    "still goes through normal tool approvals."
                ),
                parameters={
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _run_now,
        )

        async def _remind(minutes: int, note: str,
                          timezone: str | None = None) -> dict[str, Any]:
            # 0.7.0 (luna 075/phase5): one-shot sugar over trigger_create.
            # No service grammar change needed: the absolute fire time becomes
            # a specific yearly cron (min hour dom mon *) + max_runs=1.
            try:
                minutes = int(minutes)
            except (TypeError, ValueError):
                return {"error": "minutes must be an integer >= 1"}
            if minutes < 1:
                return {"error": "minutes must be an integer >= 1"}
            from datetime import datetime, timedelta
            from zoneinfo import ZoneInfo

            tz = timezone or "UTC"
            try:
                now = datetime.now(ZoneInfo(tz))
            except Exception:  # noqa: BLE001
                return {"error": f"unknown timezone '{tz}'"}
            fire_at = now + timedelta(minutes=minutes)
            if fire_at.second or fire_at.microsecond:
                # ceil to the next whole minute: cron has no seconds, and a
                # fire time already inside the current minute would roll the
                # yearly cron over to NEXT year.
                fire_at += timedelta(minutes=1)
                fire_at = fire_at.replace(second=0, microsecond=0)
            cron = f"{fire_at.minute} {fire_at.hour} {fire_at.day} {fire_at.month} *"
            out = await _create(
                name=f"reminder: {note[:60]}",
                schedule_expr=cron,
                action_type="agent_prompt",
                target=(
                    f"Reminder you set for yourself {minutes} minutes ago: {note}. "
                    "Act on it now, or tell the owner if it was for them."
                ),
                timezone=tz,
                max_runs=1,
                purpose=f"one-shot reminder: {note[:80]}",
            )
            if "error" not in out:
                out["fires_at"] = fire_at.isoformat()
            return out

        _reg(
            ToolDef(
                name="remind_me",
                description=(
                    "Set a one-shot reminder: in `minutes` minutes a scheduled "
                    "fire hands you `note` as a prompt (full agent turn — you "
                    "can act, not just speak). Survives restarts and fires even "
                    "while this Luna sleeps. Sugar over trigger_create with "
                    "max_runs=1; pass the owner's IANA timezone if known."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "minutes": {"type": "integer", "minimum": 1,
                                    "description": "How many minutes from now."},
                        "note": {"type": "string",
                                 "description": "What to do / remember when it fires."},
                        "timezone": {"type": "string",
                                     "description": "IANA tz, e.g. Asia/Jerusalem."},
                    },
                    "required": ["minutes", "note"],
                },
                modes=_WRITE_MODES,
                policy="auto_approve",
                risk_level="low",
            ),
            _remind,
        )


__all__ = ["SchedulerPlugin", "TOOL_NAMES"]
