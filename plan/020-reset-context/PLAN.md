# 020 — Reset context from Chat settings (plugin-chat-ui 0.27.0)

## Context

Luna core 0.92.014 (plan 099 there) adds
`POST /api/conversations/{id}/reset-context`: sets the condense watermark to
the newest message and replaces the recap with a one-line reset note. The
transcript stays; the model's next turn starts fresh. Built for the polluted
ops chat (singleton, undeletable), useful for any chat. The owner wants the
control in the conversation's own settings — the 019 ChatSettingsDialog.

## Goals

1. "Reset context" row in ChatSettingsDialog, every chat kind (ops
   included — ops is the reason this exists), with a two-step confirm and
   copy that says messages are kept, only the model's working context resets.
2. `/reset` composer command beside `/condense` (same busy-guard), system
   line reporting the outcome.
3. Graceful degrade against an older core (404 → "This Luna doesn't support
   context reset yet"), no crash.

## Non-Goals

- No deletion, no archive, no server-side settings persistence.
- No automatic client-side triggering (the core does the upgrade auto-reset).
- No BasicChat (core fallback panel) work — that lives in the luna repo.

## Approach

- `src/lib/api.ts` (chat-ui's client): `resetContext(conversationId)` →
  POST `/api/conversations/{id}/reset-context`.
- `ChatSettingsDialog`: new section between Message color and Delete —
  label "Reset context", body copy, button flips to a confirm state
  ("Reset — keep messages") like the delete confirm; on success closes the
  confirm, fires an `onReset` callback with the returned context payload so
  ChatPanel can refresh the gauge and drop a system line.
- ChatPanel: handle `/reset` in the same command block as `/condense`
  (busy-guard, no-conversation guard); on success
  `addSystemLine('Context reset — earlier messages stay in the transcript
  but are no longer sent to the model.')`; 404 → the degrade line.

## Data/API contract

Request: `POST /api/conversations/{id}/reset-context` (auth as usual).
Response 200: `{ reset: boolean, context: ContextStatusPayload }` —
`reset=false` means nothing to reset (empty conversation). 404: unknown
conversation OR an older core without the route (same degrade line covers
both). No request body.

## Risks

- Older core 404 — handled (degrade line).
- A reset mid-turn: composer busy-guard prevents `/reset` during a stream,
  and the dialog action is disabled while a turn is in flight (same
  condition the composer uses).

## Acceptance criteria

- Dialog shows the row for building AND ops chats; confirm resets and the
  context gauge drops; transcript unchanged on screen.
- `/reset` works and reports; on a fake 404 the degrade line appears.
- vitest suite green (new 020 test file), tsc + vite build clean,
  chat-ui-gate passes.

## Verification

vitest + tsc + build + gate; QA against a live Luna on core 0.92.014 if one
is up (dialog reset on the ops chat, gauge drop, transcript intact); publish
0.27.0 to marketplaces.com.ai official.
