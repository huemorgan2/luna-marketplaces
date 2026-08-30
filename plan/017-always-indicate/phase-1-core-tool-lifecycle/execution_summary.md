# Phase 1 — execution summary

## Shipped

Luna core **0.88.003**, commit `886b4628` on `main` (pushed).

- `luna/agent/runtime.py` — the approval wrapper now emits `tool.called`
  three times per call, all with the same `call_id`: `status: "pending"`
  the moment the wrapper is entered (before pre-gate, approval, policy and
  vault-ref checks), `status: "awaiting_approval"` right before
  `_approvals.request(...)`, and `status: "running"` right before the
  handler runs (this replaces the old single emit that fired after the
  gates). `tool.completed` statuses unchanged (`ok | skipped | rejected |
  error`).
- `plugins/plugin_api/app.py` — `_tool_hint(arguments)` returns one safe
  scalar (preferred keys `name, title, label, query, q, path, file, filename,
  url, board, board_id, id, skill, tool, table, channel, to` first, then
  argument order; keys matching `token|secret|password|passwd|key|credential|
  auth|cookie|value|content|body|text` skipped; `vault:` refs, multi-line and
  empty strings skipped; strings cut at 40 chars with `…`; ints/floats
  stringified; booleans ignored). `_project_tool_frame` adds it as `hint`.
  `arguments` still never reach the browser.
- `tests/090-one-tool-row/test_lifecycle.py` (new, 17 tests): success is
  pending→running→ok; pre-gate skip is pending + completed(skipped,
  pre_gate_check); policy=block is pending + completed(rejected); frame
  carries hint never arguments; hint rule table. Existing 090 tests adapted
  to the upsert (pair helper accepts 1 or 2 called events; subagent test
  asserts pending/running; projection test expects the hint).

## Verified

- `pytest tests/090-one-tool-row`: 25 passed.
- Full core suite (`tests`, dojo excluded): 2356 passed, 6 failed, 73
  skipped. All 6 failures are pre-existing and unrelated to this phase —
  they come from other plugin checkouts present in the working tree
  (`plugin-goalseek`'s `goal_ratify` tripping the approval-word security
  test, `plugin_mcp` missing its SettingsTab.tsx, plugin-curiosity drive
  occupancy, plugin_files decoupling/storage tests against a newer
  plugin_files). None touch runtime/app.py/090.
- Real environment: isolated QA Luna on :8951 running this core with
  plugin_chat_ui 0.22.0 — see phase 2 summary for the browser log
  (`pending → running → done` with hint `25`; `pending → awaiting →
  rejected` with hint `prod`).

## Deviations from PHASE.md

- Added `label` to the preferred hint keys after the browser run showed
  `slow_wait(seconds=25, label="coffee break")` hinting `25`.
- No SSE probe script run; the browser observer log covered the same
  frames end to end.

## Reassessment of remaining phases

No changes. Phase 3 (publish) folds into phase 2 — shipped in one sitting.
