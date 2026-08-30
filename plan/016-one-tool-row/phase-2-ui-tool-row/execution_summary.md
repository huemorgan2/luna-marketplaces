# Phase 2 — UI ToolRow: execution summary

## What shipped

Nothing published yet — by design phases 2 and 3 land as one plugin
version (see PHASE.md). Code is in the working tree of
`marketplace-src/plugin_chat_ui`, committed together with phase 3.

Files (`ui-src/src/views/ChatPanel.tsx`):

- `ToolLogEntry` is now `{ id, name, status, error? }` keyed by the
  server's `call_id`.
- `logToolCalled` / `logToolCompleted` replace the name-based
  `logToolCalls` / `logToolResult`. `handleUiEvent` routes the `tool.called`
  and `tool.completed` `ui_event` frames (from core 0.88.000) into them;
  `ok` / `skipped` settle to `done`, anything else to `error` with the
  server's `error` (or `reason`) kept for the chip title. Duplicate
  `call_id`s are ignored. Reset at the next user turn and the
  stream-closed "settle running chips" guard are unchanged.
- New `ToolRow` component renders at the slot the wrench pill used: one
  wrap-row, consecutive same-tool calls cluster into one chip with `#2 #3`
  tags (same shape as history receipts), labels via `humanizeTool`. A chip
  (or a `#n` tag) carries `shimmer-text` while its call runs, `text-ink-500`
  when done, rose when errored (message on hover). Subagent tool calls are
  ordinary frames with the parent conversation id, so they land in the
  same row.
- Composer: `WorkingTools` (spinner + names) became `WorkingLine`
  (spinner + "working…"), shown whenever the turn streams.
- The 089 live-receipt suppression (`liveAutoIdsRef`) is untouched and now
  actually works, because `approval.decided` carries `conversation_id`
  since core 0.88.000 — so a tool shows once during the live turn, and the
  approvals-fed `AutoToolReceipts` rows remain the history view after a
  reload.

Tests: `ui-src/src/__tests__/090-tool-row.test.tsx` — 7 tests (chip on
`tool.called` with shimmer → grey on `tool.completed`; rose + title on
error and duplicate ids ignored; clustering with `#2` and subagent calls in
the same row; row survives turn end and resets on the next turn; stream
closing mid-call settles the chip; composer shows only "working…"; an
auto-approval decided with the live conversation id adds no receipt row).
`012-conversation-isolation` was adapted from `working-tool-chips` to
`working-line` + `tool-row` (the row must not leak into a new chat).

## Verification

- vitest: 18 files, **115 passed, 4 failed** — the 4 failures are all
  `089-tool-chip.test.tsx`, which asserts the wrench pill that this phase
  replaces; it is retired in phase 3 (baseline was 112 passed).
- `npm run build` (`tsc -b`) fails on exactly the code phase 3 deletes:
  unused `ToolProgressChip`, unused `toolNames` prop, the emptied
  `onToolResult` callback, plus an unused `fireEvent` import in the new
  test. **Real-Luna browser verification is therefore deferred to phase 3**,
  which ships the same bundle — recorded as a deviation, not skipped.

## Deviations from PHASE.md

- Browser verification moved to phase 3 (above).
- Version target: the other session published plugin-chat-ui 0.20.0 and
  0.20.1 while this phase ran (`e59319b`, `5e8ac85`, `3e75f7a`), so phases
  2+3 ship as **0.21.0**, not 0.20.0.

## Surprises / learnings

- The other session is editing the same plugin checkout (it bumped
  `luna-plugin.toml` to 0.20.1 mid-phase). My edits were applied by
  read-modify-write scripts with exact-match asserts, so nothing of theirs
  was clobbered; `git diff` for the plugin shows only the 016 hunks plus
  the 012 test adaptation.

## Reassessment of remaining phases

- Phase 3 scope **changes**: keep `liveAutoIdsRef` / `suppressAutoIds`
  (they are the "show a tool once, hydrate from approvals on reload"
  mechanism and now work). Remove only: `ToolProgressChip`, `WorkingTools`
  remnants, `TurnUI.toolNames` and every `onToolCall` name feed, the
  `toolNames` Composer prop, the `089-tool-chip` test. PLAN.md updated
  accordingly (marked "changed by phase 2").
- Phase 3 also carries the real-Luna browser check for the whole feature
  and ships 0.21.0; phase 4 (publish) folds into the same step if the build
  and browser check pass in phase 3 — otherwise stays separate.
