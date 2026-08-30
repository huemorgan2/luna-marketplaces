# Phase 2 — UI ToolRow (plugin_chat_ui)

## Baseline

- marketplace-src/plugin_chat_ui at 0.19.0; vitest: 17 files / 112 tests pass
  (recorded in phase 1). Core 0.88.000 (phase 1) provides `ui_event` frames
  `tool.called` / `tool.completed` with `call_id`, `name`, `status`,
  `error`, `conversation_id`; `approval.decided` now carries
  `conversation_id`.
- Requires core ≥ 0.88.000 — on older cores no tool frames arrive and the
  row simply stays empty (the composer still says "working…").

## Scope

1. **State**: `ToolLogEntry` becomes `{ id, name, status, error? }` keyed by
   `call_id`. `handleUiEvent` handles `tool.called` (append, status
   `running`; ignore duplicate ids) and `tool.completed` (`status: ok |
   skipped` → `done`, anything else → `error`, carry `error`/`reason`).
   The legacy name-based `logToolCalls` / `logToolResult` stop feeding the
   log (they are deleted in phase 3 together with `toolNames`). Per-
   conversation storage, reset at the start of the next user turn, and the
   `settleToolLog` "no phantom spinners on stream close" guard stay as-is.
2. **ToolRow** component replaces `ToolProgressChip` at the same slot
   (under the timeline, above the subagent/wait lines): one wrap-row of
   chips, one chip per call cluster (consecutive calls of the same tool
   cluster with `#2 #3` tags, as `AutoToolReceipts` does). Chip text uses
   `humanizeTool`. Running → `shimmer-text` (the existing grey→white
   sweep); done → `text-ink-500`; error → rose with the error in the
   title. Subagent tool calls arrive as ordinary frames (parent
   conversation id) and land in the same row. The row persists after the
   turn ends (grey) until the next turn starts.
3. **Live receipts suppression actually works**: `liveAutoIdsRef` keeps its
   089 logic; with `conversation_id` now on `approval.decided` it fires, so
   an auto-approved tool during a live turn is shown once (ToolRow) and
   `AutoToolReceipts` rows only appear for rehydrated history (reload /
   older turns) — the "hydrate from approvals" path of the plan.
4. **Composer**: `WorkingTools` collapses to spinner + "working…" while a
   turn streams — no tool names (the row carries them). `toolNames` prop
   still exists until phase 3 deletes it.
5. Tests: `src/__tests__/090-tool-row.test.tsx` (chips appear on
   `tool.called` with shimmer; flip to grey on `tool.completed ok`; rose on
   error; repeated tool clusters with `#2`; row survives turn end and
   resets on the next turn; no `auto-tool-receipts` row for an
   auto-approval decided with the live conversation id; composer shows
   "working…" without names). The 089 chip test is retired in phase 3.

## Deliverables

- ChatPanel.tsx changes above; new test file; vitest full run green
  (112 + new).
- No version bump / publish in this phase: phase 3 removes the duplicates
  and both ship as plugin-chat-ui **0.20.0** (check the marketplace for a
  0.20.0 published by the other session first; fall back to 0.21.0).

## Verification

- vitest full suite (regressions) + new tests.
- Real Luna: build the plugin bundle, load it against an isolated
  `luna serve` (core 0.88.000, port 8951, sqlite) and drive a browser
  (playwright/CDP): send a message that calls `load_skill`, observe one
  chip shimmering then turning grey, no wrench pill, no tool names in the
  composer. Screenshot in the summary.
