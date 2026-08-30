# Phase 3 — remove the duplicate surfaces (plugin_chat_ui)

## Baseline

Phase 2 working tree: vitest 115 passed / 4 failed (all `089-tool-chip`),
`tsc -b` failing on dead code. plugin-chat-ui marketplace version 0.20.1.

## Scope

1. Delete `ToolProgressChip` and the `Check`/`ChevronDown`/`X` imports if
   they become unused.
2. Delete `TurnUI.toolNames` (+ `EMPTY_TURN.toolNames`), every
   `onToolCall` name feed (`patchTurn(... toolNames ...)`), the emptied
   `onToolResult` callbacks, the `toolNames` Composer prop and its
   destructuring.
3. Keep `liveAutoIdsRef` / `suppressAutoIds` (changed by phase 2 — see its
   summary).
4. Retire `089-tool-chip.test.tsx`; drop the unused `fireEvent` import in
   `090-tool-row.test.tsx`.
5. Version 0.21.0 in `luna-plugin.toml` and `ui-src/package.json`.

## Deliverables

- `tsc -b && vite build` clean; vitest all green (17 files).
- Real-Luna check: isolated `luna serve` (core 0.88.000, sqlite, :8951)
  with this plugin build installed; browser via playwright: send a message
  that triggers `load_skill`; observe a chip that shimmers then turns
  grey, no wrench pill, composer says only "working…"; screenshot.
- Commit + push luna-marketplaces; publish 0.21.0 (phase 4 folds in if
  green).

## Verification criteria

Build clean, tests green, screenshot showing exactly one tool surface in
the timeline during and after the turn.
