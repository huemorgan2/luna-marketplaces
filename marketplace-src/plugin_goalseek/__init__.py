"""plugin-goalseek — durable, governed goal lifecycles for Luna.

Phase 01: core lifecycle — goals with a definition of done, a stage machine,
per-goal business outcome labels over five base outcomes, evidence trail.
Phase 02: the policy engine — goal_effect (the one governed door), eight
policy families with actionable remedies, self-opening approval + persistent
grant, and self-tuning (gate hits become reflections, not just denials).
Phase 03: event-driven wakes — per-goal scheduler heartbeats, async waits
(owner reply / time / fact) that park and resume, turn-ended resume path,
concurrency cap. Goals make progress while the owner sleeps.
Phase 06: the wiki seam — goal narrative flows into the shared wiki
(curiosity's mission wiki → own wiki → local notes fallback) and namespaced
facts compound onto domain pages that the next goal reads.

Authored against ``luna_sdk`` only — never ``import luna.*``.
"""

from __future__ import annotations

import logging

from luna_sdk import LunaPlugin, PluginContext, PluginManifest, SidebarSection, ToolDef

from .effects import EffectsBridge
from .grants import GrantStore
from .knowledge import Knowledge
from .models import ALL_TABLES
from .prompt import goals_fragment
from .settings import SettingsStore, register_config_section
from .tools import GoalseekStore, make_handlers
from .wake import GoalseekRuntime
from .waits import WaitStore

log = logging.getLogger("plugin-goalseek")

__version__ = "0.6.0"

_TOOL_DEFS: list[ToolDef] = [
    ToolDef(
        name="goal_open",
        description=(
            "Open a goal-seek goal: a concrete outcome with a definition of done. "
            "Use for outcomes worth pursuing over days/weeks — not for one-off tasks. "
            "outcome_labels lets the goal define business end-states (e.g. "
            "{'deal signed': 'achieved', 'disqualified': 'failed'}). "
            "opened_by='agent' (self-opening) raises an owner approval card, unless "
            "the owner granted 'auto_approve_agent_goals' — then it opens silently."
        ),
        parameters={
            "type": "object",
            "properties": {
                "statement": {"type": "string", "description": "What the goal is, one sentence"},
                "definition_of_done": {"type": "string", "description": "The objective test that the goal is achieved"},
                "deadline": {"type": "string", "description": "ISO date/datetime, optional"},
                "autonomy_level": {"type": "integer", "description": "1 observe & advise, 2 act with approval, 3 act freely (default)"},
                "risk_ceiling": {"type": "string", "enum": ["low", "medium", "high"]},
                "outcome_labels": {
                    "type": "array",
                    "description": "Business end-states for this goal, each mapped to a base outcome",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "Business label, e.g. 'deal signed'"},
                            "outcome": {"type": "string", "enum": ["achieved", "failed", "abandoned", "escalated", "expired"]},
                        },
                        "required": ["label", "outcome"],
                    },
                },
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Initial plan steps, optional"},
                "opened_by": {"type": "string", "enum": ["owner", "agent"], "description": "Who is opening this goal. Use 'agent' when self-opening."},
                "cadence": {"type": "string", "description": "Heartbeat cadence — how often the goal self-wakes to make progress, e.g. 'every day at 09:30' (default), 'every 2 hours', 'every weekday at 08:00'"},
            },
            "required": ["statement", "definition_of_done"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_get",
        description="Read one goal in full: stage, steps, facts (with authority), recent evidence events.",
        parameters={
            "type": "object",
            "properties": {"goal_id": {"type": "string"}},
            "required": ["goal_id"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_list",
        description="List goals. By default open goals only; stage= filters, include_closed=true adds history.",
        parameters={
            "type": "object",
            "properties": {
                "stage": {"type": "string", "enum": ["proposed", "active", "parked", "waiting", "closing", "closed"]},
                "include_closed": {"type": "boolean"},
            },
            "required": [],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_update",
        description=(
            "Update a goal: statement/definition_of_done/deadline/stage/cadence, append a "
            "progress note, add a plan step (step_add), or set a step's status "
            "(step_id + step_status: planned|active|done|ghost — ghost = abandoned branch). "
            "cadence sets how often the goal self-wakes (its heartbeat schedule). "
            "Moving stage to 'closed' is refused — use goal_close."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "statement": {"type": "string"},
                "definition_of_done": {"type": "string"},
                "deadline": {"type": "string"},
                "stage": {"type": "string", "enum": ["proposed", "active", "parked", "waiting", "closing"]},
                "note": {"type": "string", "description": "Progress note appended to the evidence trail"},
                "step_add": {"type": "string", "description": "Title of a new plan step to append"},
                "step_id": {"type": "string"},
                "step_status": {"type": "string", "enum": ["planned", "active", "done", "ghost"]},
                "cadence": {"type": "string", "description": "Heartbeat cadence, e.g. 'every day at 09:30', 'every 2 hours'; empty string resets to default"},
            },
            "required": ["goal_id"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_fact_set",
        description=(
            "Store/update a structured fact on a goal (agent authority). Facts with "
            "human/system authority are read-only to the agent."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "key": {"type": "string"},
                "value": {
                    "type": "string",
                    "description": "The fact value — plain text, or JSON text for structured values",
                },
                "source": {"type": "string", "description": "Where this fact came from"},
            },
            "required": ["goal_id", "key", "value"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_close",
        description=(
            "Close a goal with an explicit terminal outcome "
            "(achieved|failed|abandoned|escalated|expired), an optional business "
            "outcome_label defined at goal_open, and a structured reason "
            "({summary, cause?}). Closed goals are immutable."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "outcome": {"type": "string", "enum": ["achieved", "failed", "abandoned", "escalated", "expired"]},
                "outcome_label": {"type": "string"},
                "reason": {
                    "type": "object",
                    "description": "{summary: str, cause?: str (required on failed)}",
                    "properties": {
                        "summary": {"type": "string", "description": "Why the goal closed, one sentence"},
                        "cause": {"type": "string", "description": "Required on failed: what caused the failure"},
                    },
                    "required": ["summary"],
                },
            },
            "required": ["goal_id", "outcome", "reason"],
        },
        policy="prompt_always",
        risk_level="medium",
    ),
    ToolDef(
        name="goal_effect",
        description=(
            "Take a consequential action FOR a goal through the governed door: "
            "the goal's policies are evaluated first (working hours, cooldowns, "
            "consent, risk ceiling, autonomy level, …). If blocked you get a "
            "reason AND a remedy — follow the remedy (usually: gather a missing "
            "fact) instead of retrying. Declare honestly: risk, contact/channel "
            "for outreach, target + is_write for writes, writes_fact when the "
            "action updates a goal fact. Governance happens inside this tool, "
            "which is why it is not itself approval-gated for low/medium risk. "
            "kind='playbook_run' with playbook=<name> fires a whole playbook "
            "as one governed step (args become the playbook inputs); the run's "
            "real result is recorded as evidence — a failed run never confirms "
            "the step."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["tool_call", "playbook_run"], "description": "tool_call (default) executes one tool; playbook_run fires a whole playbook"},
                "tool": {"type": "string", "description": "Name of the tool to execute (kind=tool_call)"},
                "playbook": {"type": "string", "description": "Playbook name to run (kind=playbook_run)"},
                "intent": {"type": "string", "description": "One sentence: why this action serves the goal"},
                "args": {"type": "string", "description": "JSON object text with the tool's arguments or the playbook's inputs, e.g. '{\"query\": \"...\"}'"},
                "risk": {"type": "string", "enum": ["low", "medium", "high"], "description": "Honest risk of this action (default medium)"},
                "contact": {"type": "string", "description": "Person/org being contacted, when this is outreach"},
                "channel": {"type": "string", "description": "Outreach channel, e.g. email, phone, whatsapp"},
                "target": {"type": "string", "description": "The thing being read/written, e.g. a doc id or CRM record"},
                "is_write": {"type": "boolean", "description": "True when the action changes external state (playbook runs are always writes)"},
                "writes_fact": {"type": "string", "description": "Goal fact key this action updates, if any"},
            },
            "required": ["goal_id", "intent"],
        },
        policy="auto_approve",
        risk_level="medium",
    ),
    ToolDef(
        name="goal_policy_set",
        description=(
            "Add or update a policy rule (global when goal_id omitted, else "
            "goal-scoped). Families: lifecycle, permission, eligibility, "
            "consent, timing, sequencing, data_authority, approval. Example "
            "timing params: {\"working_hours\": \"9-17\", \"cooldown_minutes\": 60, "
            "\"window_cap\": 5, \"window_hours\": 24}. Owner-approved."
        ),
        parameters={
            "type": "object",
            "properties": {
                "family": {"type": "string", "enum": ["lifecycle", "permission", "eligibility", "consent", "timing", "sequencing", "data_authority", "approval"]},
                "params": {"type": "string", "description": "JSON object text with family-specific settings"},
                "goal_id": {"type": "string", "description": "Scope to one goal; omit for a global rule"},
                "enabled": {"type": "boolean"},
            },
            "required": ["family", "params"],
        },
        policy="prompt_always",
        risk_level="medium",
    ),
    ToolDef(
        name="goal_grant_revoke",
        description=(
            "Revoke a persistent goal-seek grant (e.g. 'auto_approve_agent_goals' "
            "— after revoking, Luna's self-opened goals need an approval card again)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {"type": "string", "enum": ["auto_approve_agent_goals"]},
            },
            "required": ["key"],
        },
        policy="prompt_always",
        risk_level="high",
    ),
    ToolDef(
        name="goal_wait",
        description=(
            "Park a goal until the world answers: the goal moves to 'waiting', "
            "its heartbeat pauses, and it resumes automatically when the "
            "condition resolves. kind='owner_reply' waits for the owner's next "
            "message; 'until_time' waits for a moment (pass until=ISO datetime); "
            "'fact_present' waits for goal_fact_set to write fact_key; 'manual' "
            "waits for goal_wait_resolve. Use instead of polling or idling."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "description": {"type": "string", "description": "What you are waiting for, one sentence"},
                "kind": {"type": "string", "enum": ["owner_reply", "until_time", "fact_present", "manual"]},
                "until": {"type": "string", "description": "ISO datetime the wait resolves at (until_time only)"},
                "fact_key": {"type": "string", "description": "Goal fact key that resolves the wait (fact_present only)"},
                "timeout": {"type": "string", "description": "ISO datetime after which the wait times out and the goal resumes anyway"},
                "keep_heartbeat": {"type": "boolean", "description": "Keep the heartbeat firing while waiting (default false)"},
            },
            "required": ["goal_id", "description", "kind"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_wait_resolve",
        description=(
            "Resolve (or cancel with cancel=true) an open goal wait — the goal "
            "moves back to 'active' when no other waits remain. until_time and "
            "owner_reply waits usually resolve themselves; use this for "
            "'manual' waits or to unblock early."
        ),
        parameters={
            "type": "object",
            "properties": {
                "wait_id": {"type": "string"},
                "note": {"type": "string", "description": "Why/how it resolved"},
                "cancel": {"type": "boolean", "description": "Cancel instead of resolve (the wait no longer matters)"},
            },
            "required": ["wait_id"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
    ToolDef(
        name="goal_note",
        description=(
            "Write durable narrative knowledge for a goal: what you learned, "
            "why a route was abandoned, context worth keeping. Lands on the "
            "goal's wiki page when a wiki is installed (mission wiki if "
            "curiosity is bound, else Goal-Seek's own), otherwise in local "
            "notes — same call either way. Use goal_fact_set for structured "
            "values; use this for prose."
        ),
        parameters={
            "type": "object",
            "properties": {
                "goal_id": {"type": "string"},
                "title": {"type": "string", "description": "Short heading for the note"},
                "body_md": {"type": "string", "description": "The note, markdown"},
            },
            "required": ["goal_id", "title", "body_md"],
        },
        policy="auto_approve",
        risk_level="low",
    ),
]


class GoalseekPlugin(LunaPlugin):
    manifest = PluginManifest(
        name="plugin-goalseek",
        shown_name="Goal-Seek",
        version=__version__,
        description=(
            "Durable, governed goal lifecycles: concrete outcomes with a "
            "definition of done, explicit terminal outcomes, and an honest "
            "evidence trail."
        ),
        # Soft in managed dirs (load-order only) — the code feature-detects the
        # trigger_* tools anyway, so goal-seek still loads without a scheduler.
        depends_on=["plugin-scheduler"],
        event_subscriptions=["agent.turn.ended"],
        db_tables=[t.name for t in ALL_TABLES],
        # Phase 05: the Goals pane.
        routes_module="routes",
        sidebar_sections=[
            SidebarSection(id="goals", label="Goals", icon="target",
                           sort_order=46, path="ui/"),
        ],
    )

    def __init__(self) -> None:
        self._store: GoalseekStore | None = None
        self._grants: GrantStore | None = None
        self._effects: EffectsBridge | None = None
        self._waits: WaitStore | None = None
        self._runtime: GoalseekRuntime | None = None
        self._settings: SettingsStore | None = None
        self._knowledge: Knowledge | None = None

    async def on_load(self, ctx: PluginContext) -> None:
        async with ctx.engine.begin() as conn:
            for table in ALL_TABLES:
                await conn.run_sync(table.create, checkfirst=True)
        await self._migrate(ctx)
        self._grants = GrantStore(ctx.db_session_factory)
        self._settings = SettingsStore(ctx.db_session_factory, grant_store=self._grants)
        self._store = GoalseekStore(ctx.db_session_factory, settings=self._settings)
        self._waits = WaitStore(ctx.db_session_factory)
        self._runtime = GoalseekRuntime(ctx, self._store, self._waits)
        # Phase 06: the wiki seam — provider re-checked per call, so
        # installing/removing wiki or curiosity needs no goalseek restart.
        self._knowledge = Knowledge(
            ctx.db_session_factory,
            provider_registry=getattr(ctx, "provider_registry", None),
        )
        self._effects = EffectsBridge(
            ctx.db_session_factory,
            tool_registry=ctx.tool_registry,
            approvals=ctx.approvals,
            plugin_name=self.manifest.name,
            # Phase 04: long playbook runs park the goal on a wait; the
            # completion callback resolves it and wakes the goal.
            waits=self._waits,
            runtime=self._runtime,
            settings=self._settings,
            knowledge=self._knowledge,
        )
        # Phase 05: one config section, two doors (Settings tab + manage_config).
        register_config_section(ctx, self._settings, self.manifest.name)
        from . import routes as routes_mod

        routes_mod.init_routes(
            store=self._store, waits=self._waits, settings=self._settings,
            grants=self._grants, runtime=self._runtime,
            tool_registry=ctx.tool_registry,
            session_factory=ctx.db_session_factory,
            knowledge=self._knowledge,
        )
        handlers = make_handlers(
            self._store,
            effects=self._effects,
            grant_store=self._grants,
            approvals=ctx.approvals,
            waits=self._waits,
            runtime=self._runtime,
            knowledge=self._knowledge,
        )
        for tool_def in _TOOL_DEFS:
            ctx.tool_registry.register(self.manifest.name, tool_def, handlers[tool_def.name])
        # Phase 03: resume path — owner replies / due waits wake their goals.
        events = getattr(ctx, "events", None)
        if events is not None and callable(getattr(events, "subscribe", None)):
            events.subscribe("agent.turn.ended", self._runtime.on_turn_ended)
        # Durability: repair per-goal triggers after restart (best-effort;
        # plugin-scheduler may load after us, so failures here are normal).
        try:
            report = await self._runtime.sync_all()
            log.info("trigger sync on load: %s", report)
        except Exception as e:  # noqa: BLE001
            log.warning("trigger sync on load failed: %s", e)
        log.info("plugin-goalseek %s loaded (%d tools)", __version__, len(_TOOL_DEFS))

    @staticmethod
    async def _migrate(ctx: PluginContext) -> None:
        """Additive column upgrades for pre-0.3.0 installs. ``checkfirst``
        table creation doesn't add columns to existing tables, and plugins
        don't run alembic — so: idempotent ALTERs, errors swallowed (SQLite
        tests create fresh tables that already have the column)."""
        from sqlalchemy import text

        try:
            async with ctx.engine.begin() as conn:
                await conn.execute(text(
                    "ALTER TABLE goalseek_goals ADD COLUMN IF NOT EXISTS cadence VARCHAR(64)"
                ))
        except Exception as e:  # noqa: BLE001 — sqlite lacks IF NOT EXISTS; column may exist
            log.debug("cadence column migration skipped: %s", e)

    async def prompt_sections(self) -> list[str]:
        if self._store is None:
            return []
        try:
            goals = await self._store.list(include_closed=False)
            reflections = await self._tuning_reflections(goals)
        except Exception as e:  # noqa: BLE001 — a broken DB must not kill prompts
            log.warning("prompt_sections failed: %s", e)
            return []
        frag = goals_fragment(goals, reflections)
        return [frag] if frag else []

    async def _tuning_reflections(self, goals: list[dict]) -> dict[str, str]:
        """Per-goal tuning reflections for goals that keep hitting gates."""
        from . import tuning

        out: dict[str, str] = {}
        for g in goals[:5]:
            if g["stage"] != "active":
                continue
            full = await self._store.get(g["id"])
            events = full.get("recent_events") or []
            stats = tuning.gate_stats(events)
            reflection = tuning.reflection_text(stats, tuning.recent_remedies(events))
            if reflection:
                out[g["id"]] = reflection
        return out
