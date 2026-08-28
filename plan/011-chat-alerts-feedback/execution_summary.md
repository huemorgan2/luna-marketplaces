# 011 — execution summary

All three stages shipped 2026-08-28, no luna-core change.

## Stage 1 — plugin-chat-ui 0.14.0 (this repo, a32bc93)

- `ui-src/src/lib/chatAlerts.ts`: `FRUSTRATION_PATTERNS` (14 regexes, curly
  apostrophes handled), `matchesFrustration()`, and localStorage suppression
  `chatui.alert.agent-feedback.<convId>` (24h, dismiss = answer = same key).
- `ChatPanel.tsx`: exported `AgentFeedbackBanner` (sticky top-0 inside the
  scroll container, `max-w-xl w-fit mx-auto`, one line, Good/Mediocre/Bad
  pills + ✕); scroll container's top padding moved to a spacer so alerts sit
  flush; `api.uiPlugins?.()` gating on `plugin-feedback`; frustration check in
  `send()` on the user's own typed text (bridge/commands skipped); verdict
  posts `luna-navigate` section `feedback`, target
  `compose?verdict=<v>&conversation=<id>`.
- Tests: `011-agent-feedback-alert.test.tsx` — 93/93 green.
- Published: marketplaces.com.ai official, catalog latest 0.14.0.

## Stage 2 — plugin-feedback 0.4.0 (huemorgan2/plugin-feedback b32dd8b)

- `ui/app.js`: 'navigate' plugin-event handler + `?compose=1` URL fallback →
  compose prefilled (title `Agent behaviour: <verdict>`, body
  `The agent is doing a <verdict> job.\n`, cursor on the next line), category
  praise/frustration via new visible "Agent behaviour" chip; conversation id
  kept for stage 3.

## Stage 3 — plugin-feedback 0.5.0 (65baeb5)

- Compose checkbox "Send the conversation context", checked on every open.
- `routes.py`: `NewTicketBody.conversation_id/include_context`; transcript
  appended to the ticket body under
  `--- conversation context (last 30 messages) ---`.
- `context.py`: `transcript_block()` — last 30 user/assistant messages via
  `ctx.conversations`, newest-kept caps (2k/message, 24k total), `scrub()`ed;
  `_latest_conversation_id()` SQL fallback when no deep-link id. All
  best-effort: context failure never blocks the ticket.
- Tests: `tests/test_transcript.py` (8 new) — 64/64 green.
- Published: catalog latest 0.5.0.

## Deviations from plan

- None functional. The superseded core-phase draft (luna plans/088) was
  discarded uncommitted.

## Verification still owed

Live tenant pass (CDP browser): banner → Bad → prefilled compose → ticket
body carries transcript — pending plugin upgrades on the agent(s).
