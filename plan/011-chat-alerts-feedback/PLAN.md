# 011 — Chat alerts + agent-behaviour feedback prompt

Three shippable stages, no luna-core change. Canonical plan for work spanning
plugin-chat-ui (this repo) and plugin-feedback (luna-plugins repo).

## Goal

1. A Cursor-style **one-line alert bar** at the top of the messages area:
   sticky inside the scroll area, flush (no black gap above), centered and
   narrower than the message column, truncating text, inline action pills,
   `✕` to dismiss.
2. First alert: when the user's outgoing messages show **frustration**
   (regex only, no LLM), ask **"Is the agent doing a good job?"** with
   **Good / Mediocre / Bad**. A click opens the Feedback compose pane with
   the body prefilled `"The agent is doing a {verdict} job."` and the cursor
   on the next line; the ticket carries the last 30 conversation messages.
3. Shows only when plugin-feedback is installed & enabled.

## Decision record

- **No core change.** The generic `[[chat_alerts]]` manifest slot is
  deferred until a second consumer exists. chat-ui hardcodes this one alert
  and gates it on plugin-feedback's presence in `GET /api/ui/plugins`
  (`api.uiPlugins()`, entry `name === 'plugin-feedback'`).
- **Detection is client-side in chat-ui** at the send path (`send()`,
  ChatPanel ~1660) — the trigger input is the message just typed; regex is
  cheap; no server round-trip, no SSE channel.
- **Navigation** uses existing plumbing end-to-end: chat-ui posts
  `{type:'luna-navigate', section:'feedback', target:'compose?verdict=bad&conversation=<id>'}`
  → Shell validates the section and delivers
  `{type:'luna-plugin-event', event:'navigate', payload:{target}}` to the
  pane iframe, buffered until its `luna-ui-ready` handshake (which
  plugin-feedback's app.js already sends). Verified in Shell.tsx:348-389.
- **Context attach is server-side** in plugin-feedback's own route, embedded
  into the ticket BODY under a divider — zero luna-service/control-plane
  change (luna-service is read-only for us by default).

---

## Stage 1 — plugin-chat-ui 0.14.0: alert surface + trigger

Files: `marketplace-src/plugin_chat_ui/ui-src/src/views/ChatPanel.tsx`,
new `ui-src/src/lib/chatAlerts.ts`, tests.

1. **`lib/chatAlerts.ts`** (exported for tests):
   - `FRUSTRATION_PATTERNS: RegExp[]` — compiled with `i`:
     profanity (`\b(fuck\w*|wtf|bullshit|goddamn)\b`), `!{3,}`,
     "you're not hearing/listening/getting/reading me",
     "not what i asked/said/meant/wanted",
     "did you/u (actually/even) do/fix/change/update/read …?",
     `^well\b.{0,40}\?`, "again/still broken/wrong/not working/the same",
     "stop doing/saying/repeating".
   - `matchesFrustration(text: string): boolean`.
   - `alertSuppressedUntil(convId) / suppressAlert(convId, hours=24)` —
     localStorage key `chatui.alert.agent-feedback.<convId>`, epoch ms;
     dismiss and cooldown share the key. try/catch around storage.
2. **Gating**: on mount, `api.uiPlugins()` → `hasFeedback` state
   (`entries.some(e => e.name === 'plugin-feedback')`). Fetch failure →
   false, never blocks the composer.
3. **Trigger**: in `send()`, after the text is accepted for sending: if
   `hasFeedback && matchesFrustration(text) && !suppressed(convId)` →
   `setFeedbackAlertConv(convId)`.
4. **Layout** (the black-gap fix): messages scroll container (~2155) drops
   its top padding (`py-6`→`pb-6`, dense `py-3`→`pb-3`). First child inside
   it: the sticky alert bar (`sticky top-0 z-30`); the rest of the content
   is wrapped with `pt-6`/`pt-3` so nothing moves when no alert shows.
5. **Banner** (one line, Cursor-like, ux_guidelines flat-ink):
   `mx-auto max-w-xl w-fit`, `flex items-center gap-2`, `bg-ink-800`
   hairline `border border-white/10`, `rounded-lg`, `px-3 py-1.5`,
   13px text `truncate`; pills `Good | Mediocre | Bad` (rounded-full,
   1px border, hover fill white/10); `✕` right (`text-ink-500` hover
   ink-200). Shows only for the active conversation it fired in.
6. **Actions**: pill click → suppress + hide + `window.postMessage({type:
   'luna-navigate', section:'feedback', target:
   `compose?verdict=${id}&conversation=${convId}`}, window.location.origin)`.
   `✕` → suppress + hide.
7. **Tests** (vitest): matcher positives/negatives (incl. plain "well"
   without question ≠ hit, "did you see the game?" IS a hit only if pattern
   says so — tune), suppression math, banner renders + dismisses, navigate
   message payload.
8. Bump toml + package.json 0.14.0; `npx vitest run --maxWorkers=2
   --testTimeout=30000`; build; commit/push (huemorgan2); package (service
   packager) + publish official; verify catalog.

## Stage 2 — plugin-feedback 0.4.0: compose deep-link

Files: `plugins/plugin-feedback/plugin_feedback/ui/app.js`, `ui/index.html`,
`luna-plugin.toml`, `pyproject.toml`.

1. **Navigate handler** in app.js: listen for
   `{type:'luna-plugin-event', event:'navigate', payload:{target}}` (plus a
   direct `?compose=1&verdict=…` URL-param fallback for deep links):
   `compose?verdict=<good|mediocre|bad>&conversation=<id>` →
   - `show('new-view')`;
   - category: `good` → `praise`, else `frustration`;
   - **new chip** `Agent behaviour` (`data-cat="frustration"`) added to the
     chips row so the selection is visible; chip active-state synced;
   - title prefill `Agent behaviour: <verdict>`;
   - body prefill `The agent is doing a <verdict> job.\n`; focus the
     textarea with the cursor at the end (next line — Roy's spec);
   - remember `conversation` in a module var for Stage 3.
2. Version 0.4.0 (toml is the authoritative stamp). Python tests untouched —
   run the plugin's pytest anyway; UI verified live in Stage 3's pass.
3. Commit in luna-plugins, push (huemorgan2 remotes as configured), package
   with `scripts/package_plugin.py`, publish official, verify catalog.

## Stage 3 — plugin-feedback 0.5.0: conversation context attach

Files: `routes.py`, `ui/index.html`, `ui/app.js`, tests.

1. **Compose checkbox** "Send the conversation context" — CHECKED every time
   the compose view opens (state reset in `show('new-view')` path, never
   persisted).
2. **Submit** includes `include_context` + `conversation_id` (from the
   deep-link, else null).
3. **routes.py**: `NewTicketBody` gains `conversation_id: str | None = None`,
   `include_context: bool = False`. When true, the route (server-side):
   - resolves the conversation (given id, else the most recently updated);
   - loads the last **30** `MessageRow`s via `ctx.db_session_factory`
     (core model import — allowed, same pattern as core routes);
   - renders a compact transcript: `[user]/[assistant]/[tool:<name>]` lines,
     each message clamped to 2 000 chars, whole transcript capped at 24 000
     chars (newest kept);
   - runs the existing `scrub()` over it (credential-shaped strings);
   - appends to the ticket body:
     `\n\n--- conversation context (last 30 messages) ---\n<transcript>`.
4. **Tests** (pytest, existing conftest fixtures): transcript render + clamp
   + scrub; include_context=false untouched body; null conversation → latest;
   missing conversation → ticket still files without context (never block
   feedback on a context error).
5. Version 0.5.0; test, commit/push, package, publish, verify catalog.
6. **Live verification** on the tenant via the CDP browser session:
   trigger with a `!!!` message, banner → Bad → compose prefilled →
   submit → ticket body carries the transcript. Remind Roy to upgrade both
   plugins on the agent(s).

## Degradation

- No plugin-feedback → `hasFeedback` false → surface never renders.
- Old plugin-feedback (0.3.x) with new chat-ui → navigate event arrives,
  pane ignores unknown event → pane simply opens on its list view. No error.
- New plugin-feedback with old chat-ui → no banner; pane unchanged otherwise.

## Out of scope

Generic `[[chat_alerts]]` manifest slot (needs a second consumer), LLM
detection, SSE-pushed alerts, structured context field on the control plane,
migrating core banners (offline etc.) onto this surface.
