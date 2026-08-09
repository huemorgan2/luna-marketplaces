# Plan 010 — Inline conversation-scoped plan card + composer widget zone

**Status:** executing (user said ship). Ships as plugin-chat-ui 0.9.0.

## Context

Two standing user complaints, both in plugin_chat_ui:

1. **The live task-plan card follows the user into every conversation** and
   floats (`sticky top-0`) over the chat. Plan 026 made that an explicit
   decision ("tasks are owner-level; the live card shows in whichever
   conversation is open", "No per-conversation task scoping" as a non-goal).
   The user wants the opposite: the card lives inline in the chat where the
   plan started and scrolls with it.
2. **The message-area redesign** in luna-plugins `plans/006-chat-composer-widgets`
   (proposal, never executed) — a transparent native-React widget zone above
   the composer, removal of the composer hairline + dark slab, and the
   Curiosity setup stepper as the first widget. Written pre-050-extraction
   against core `luna/ui/src/views/ChatPanel.tsx`; the chat UI now lives in
   `marketplace-src/plugin_chat_ui/ui-src`, so it's rebased here.

## Goals

- Live plan card renders ONLY in its home conversation, inline at its
  creation point, scrolling with the chat (no sticky, no floating chrome).
- Composer widget zone: ordered registry of self-fetching, self-hiding
  native React components rendered above `<Composer>`.
- Composer visual cleanup: drop `border-t border-white/5` and `bg-ink-950/40`
  from the composer wrapper.
- Curiosity setup stepper as the first widget: the six-dot journey rail
  (Hear → Reflect → Prove → Agree → Earn → Own) from
  `plugin-curiosity /missions/overview .journey.steps`; live-refreshed on
  `ui.plugin.event` frames; hidden when there is no mission, the journey is
  done (`mission.agent_phase === "work"`), or the fetch fails.

## Non-Goals

- No backend changes. plugin_tasks already sends `conversation_id` on every
  `ui.tasks` frame and the backfill; curiosity already serves the journey and
  emits `ui.plugin.event`.
- No slim `/journey/steps` endpoint (optional in 006; overview reuse is fine).
- No collapse animation / multi-widget stacking polish (006 Phase C).

## Approach

**Part A — plan card (done first, same release):**
`planConversationId` state threaded through all four update paths (mount
backfill, per-turn `ui.tasks` frames, global `onUiTasks` stream, 30s decay
poll) + the localStorage seed. Card renders only when it matches `activeId`
(null conversation_id falls back to rendering wherever the user is, so legacy
rows stay reachable). `sticky top-0 z-20` + backdrop/shadow removed — a
normal timeline row.

**Part B — composer widgets:**
- `ui-src/src/lib/composerWidgets.tsx` — `[{ id, Component }]` registry.
- Mount in ChatPanel inside the existing `relative` wrapper immediately
  before `<Composer>`, in a `max-w-3xl mx-auto px-6` column.
- `ui-src/src/views/composer/CuriositySetupStepper.tsx` — fetches overview
  with the shell token, subscribes via `subscribeApprovalEvents({ onPluginEvent })`,
  re-fetches on any `plugin === "plugin-curiosity"` event, renders the rail
  (done = green check, now = violet glow, todo = dim), returns null per the
  gates above.
- Composer wrapper: `border-t border-white/5 bg-ink-950/40 px-6 py-4` →
  `px-6 py-4`.

## Data/API contract

- `GET /api/p/plugin-tasks/` → `{ tasks, created_at, conversation_id, turn_active }` (existing).
- `ui.tasks` SSE frames carry `conversation_id` (existing).
- `GET /api/p/plugin-curiosity/missions/overview` → `.journey` (null without a
  mission), `.journey.steps: [{label, state: done|now|todo, when}]`,
  `.mission.agent_phase` (existing).
- `ui.plugin.event` frames → `onPluginEvent({plugin, event, payload})` (existing).

## Risks

- Existing tests fake planTasks without `conversation_id` → null → fallback
  renders in the active chat, so they stay green by design.
- Curiosity absent on an agent → fetch 404s → stepper renders null. Zone is
  invisible when empty.
- Marketplace ships from `marketplace-src` on deploy; agents pick the change
  up via marketplace upgrade of plugin-chat-ui.

## Acceptance criteria

- Live plan card: visible in its home conversation at its creation point,
  scrolls away with the chat, absent in other conversations; completed-plan
  anchors unchanged.
- Composer: no hairline / dark slab.
- Stepper: renders the 6-step rail during curiosity onboarding, disappears in
  work phase / without a mission.
- `vitest run` green, `tsc -b && vite build` clean, `chat-ui-gate.mjs` passes.

## Verification

vitest suites in `ui-src/src/__tests__` (plan-card scoping tests added in
045-plan-card.test.tsx; new composer-widgets test file), build, gate script.
