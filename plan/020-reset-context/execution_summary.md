# 020 — execution summary

Shipped plugin-chat-ui 0.27.0: per-conversation context reset, riding core
0.92.014's `POST /api/conversations/{id}/reset-context` (luna plan 099).
Nothing is deleted — the transcript stays; the model just stops seeing the
old messages (core sets the 038 condense watermark past every existing row
and swaps the recap for a one-line reset note).

## What shipped

- `/reset` composer command, beside `/condense`: a command, not a message —
  nothing goes to the agent. Busy-guard while a turn streams or a condense
  runs; success drops a system line ("Context reset — earlier messages stay
  in the transcript but are no longer sent to the model."), refreshes the
  context gauge from the returned payload; a 404 reads "This Luna doesn't
  support context reset yet."
- Chat settings dialog: a "Context" section between Message color and
  Delete — a two-step confirm ("Reset context" → "Reset — keep messages" /
  Cancel), then an inline done note. Present on EVERY chat including ops:
  the one chat that can't be deleted is the one that needs this most.
  On success the dialog calls `onReset(ctx)`, threaded through ChatHeader to
  ChatPanel, which refreshes the gauge and drops the same system line.
- `api.resetContext` + `ResetContextResult` live in core's `ui/src/lib/api.ts`
  (the `@luna/*` alias tree); the luna submodule was bumped to the 0.92.014
  commit so tsc and the build resolve them.
- Versions: `luna-plugin.toml` + `ui-src/package.json` 0.26.0 → 0.27.0.

## Verification

- vitest: 6 new tests in `020-reset-context.test.tsx` (two-step confirm
  calls the API + system line + transcript intact; section present on ops;
  Cancel disarms without a call; 404 shows the unsupported message; `/reset`
  resets without sending to the agent; reset:false reads "Nothing to
  reset"). Full suite 137/137 green.
- tsc + vite build clean; `node tools/chat-ui-gate.mjs` green.
- Published to marketplaces.com.ai official.
