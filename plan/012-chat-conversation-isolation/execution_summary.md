# 012 — Chat conversation isolation — execution summary

Shipped as plugin-chat-ui **0.15.0** (2026-08-28).

## What was done

All live-turn state in `ChatPanel.tsx` is now keyed by conversation id:

- `turns: Record<convId, TurnUI>` replaces the global `streaming` / `toolNames` /
  `subagent` / `waitLine` / `stopStage` / `condensing` states. The render derives
  the active conversation's record, so composer chrome (working… + wrenches,
  Stop button, shimmer, wait countdown, condense lock) always reflects the chat
  on screen. `streamingConvsRef: Set<convId>` and `sendBusyConvsRef` replace the
  old global sync guards.
- `messagesByConv: Record<convId, UIMessage[]>` replaces the single `messages`
  array (key `''` = pre-conversation onboarding). Delta/reasoning/live-run rAF
  buffers stay keyed by message id and flush across all conversations, so a
  background turn keeps streaming into its own timeline.
- Every stream callback in `send()` (sendMessageStream), `triggerContinuation`
  (continueConversation) and `reattachIfActive` (attachTurnStream) is scoped to
  its closure convId. `setContextStatus` and composer-input refill apply only
  when the stream's conversation is the active one.
- Global `/api/events` routing: `message.created` appends to the event's own
  conversation; events without a conversation_id are dropped (they used to land
  in whatever chat was open — the wrong-chat message bug). `chat.stopped` patches
  its own conversation's turn record.
- Continuations (queued-message drains, `needs_continuation`) now fire for their
  own conversation even after the user switches away — background turns finish
  instead of dying on switch.
- `selectConversation` no longer resets turn state; its local-* merge only ever
  sees the same conversation's in-flight bubbles. `newConversation` starts with
  a clean per-conv record. Onboarding `kickoff()` streams under `''` and hands
  off via `selectConversation`.

## Tests

`__tests__/012-conversation-isolation.test.tsx` (full ChatPanel render, mocked
api): clean composer on new chat mid-turn; stream frames stay in their own
conversation and reappear on switch-back; global message.created routes to its
own conversation and id-less events are dropped. Suite: 96/96 green, tsc clean.

## Ship

toml + package.json 0.15.0, vite build, packaged via service
`app.packaging.package_source`, published to marketplaces.com.ai (official).
