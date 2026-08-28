# 012 — Chat conversation isolation

## Problem

Switching between conversations in plugin-chat-ui is not isolated. Two user-visible bugs:

1. Opening a new chat (or any other chat) while a turn runs elsewhere leaves the
   previous chat's live-turn chrome on screen: "working…" + tool wrenches in the
   composer, the Stop button, the subagent shimmer line, the wait countdown.
2. Streamed output (assistant bubbles, deltas, notices, system lines, messages
   from the global `message.created` event) lands in whatever chat is currently
   open instead of the conversation it belongs to.

## Root cause

`ChatPanel` holds ONE global copy of all per-turn state: `messages`,
`streaming`, `toolNames`, `subagent`, `waitLine`, `stopStage`, `condensing`.
The three stream sources (`sendMessageStream` in `send()`, `attachTurnStream`
in `reattachIfActive()`, `continueConversation` in `triggerContinuation()`)
capture their conversation id in a closure but their callbacks mutate the
global state unconditionally. `selectConversation`/`newConversation` swap the
`messages` content but do not (and cannot correctly) reset the live-turn state,
and the still-running callbacks keep writing into the swapped-in view.
The "keep trailing local-* messages" merge in `selectConversation` then carries
another chat's in-flight bubbles into the new chat's timeline.

## Fix — key all turn state by conversation id

In `ChatPanel.tsx`:

1. **Per-conversation turn state.** Replace the global `streaming`/`toolNames`/
   `subagent`/`waitLine`/`stopStage`/`condensing` states with one
   `turns: Record<convId, TurnUI>` + `patchTurn(convId, patch)`. Render derives
   the active conversation's record (`turns[activeId] ?? EMPTY`), so all JSX
   stays as-is via same-named consts. A `streamingConvsRef: Set<convId>`
   replaces `streamingRef` for synchronous guards.
2. **Per-conversation messages.** Replace `messages: UIMessage[]` with
   `messagesByConv: Record<convId, UIMessage[]>` + `setConvMessages(convId, upd)`.
   The timeline renders `messagesByConv[activeId ?? '']` (key `''` = the
   pre-conversation onboarding state). Delta/reasoning/live-run flush buffers
   stay keyed by message id and apply across all conversations' arrays
   (message ids are globally unique), so a turn keeps streaming into its own
   chat while another chat is on screen — and switching back shows it.
3. **Scope every stream callback.** Thread the stream's `convId` through
   `queueDelta`/`queueReasoning`/`queueLive`/`routeUiEvent`/`handleUiEvent`/
   `addSystemLine`/`applyTurnError` and every `onDone`/`onError`/`onClosed`/
   `onQueued`/`onNotice`/`onPolicyBlocked` handler. `setContextStatus` and
   composer-input refill apply only when the stream's conversation is active.
4. **Global SSE routing.** `onMessageCreated` appends to its own
   conversation's array (was: dropped unless active — which also mis-showed
   pre-switch events). `onChatStopped` patches its own conversation's turn
   state. Rejection/secret auto-continuation guards go per-conversation.
5. **Turn continuation survives switches.** `triggerContinuation` and the
   `needs_continuation` triggers drop their "only if still active" gates —
   a queued-message drain now completes in the background conversation
   instead of dying when the user switches away.
6. **selectConversation/newConversation** stop resetting global turn state
   (none left to reset); the local-* merge now only ever sees the SAME
   conversation's in-flight bubbles. Onboarding `kickoff()` streams into the
   `''` key and hands off via `selectConversation` once the conversation exists.

Reattach behavior is unchanged: one viewer attach at a time, for the on-screen
conversation; its callbacks are now scoped so an aborted/lingering attach can
never paint another chat.

## Tests

New `__tests__/012-conversation-isolation.test.tsx` (full ChatPanel render,
mocked `@luna/lib/api` per the 045-plan-card harness):

- while a send-stream runs in c1, "New chat" shows a clean composer — no
  working-tools row, no Stop button;
- stream frames (onNewMessage/onDelta/onToolCall) arriving after the switch do
  not appear in the new chat; switching back to c1 shows them;
- a global `message.created` for c1 while c2 is open lands in c1 only.

## Ship

- bump `luna-plugin.toml` + `ui-src/package.json` to 0.15.0
- `npm test` + `npm run build` in ui-src, package via `service`
  `app.packaging.package_source`, publish to marketplaces.com.ai (official).
