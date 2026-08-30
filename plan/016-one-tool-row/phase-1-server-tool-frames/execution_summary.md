# Phase 1 — server tool frames: execution summary

## What shipped

Luna core **0.88.000**, commit `457c4ea9` on `main` (pushed). No marketplace
plugin ships in this phase — nothing user-visible changes until the chat-ui
plugin consumes the new frames (phase 2/3).

Files:

- `luna/agent/runtime.py` — every tool invocation in the main tool wrapper
  (`_wrapper`) and in the built-in `load_skill` / `load_tools` handlers
  (both the direct handlers and the `_step_*` variants) now generates one
  `call_id` (uuid4 hex) and stamps it on the `tool.called`,
  `tool.completed` and `tool.failed` payloads it emits. Every outcome
  branch is covered: pre-gate skip, approval failure, rejection, block,
  verifier error, ok, timeout, handler error.
- `luna/agent/tool_executor.py` — `execute_tool_calls` stamps the model's
  own `call.id` as `call_id` on the same three events (including the
  "unknown tool" path).
- `plugins/plugin_api/app.py` — `chat_event_stream` subscribes to
  `tool.called` and `tool.completed` for the duration of the turn and
  forwards them into the SSE as `ui_event` frames, filtered to the
  conversation being streamed. `_project_tool_frame` whitelists the fields
  that cross the wire (`type`, `call_id`, `name`, `plugin`, `status`,
  `error`, `reason`, `duration_ms`, `conversation_id`) — tool arguments and
  result previews are never forwarded.
- `luna/approval/db_impl.py`, `luna/approval/in_memory_impl.py` —
  `approval.decided` now carries `conversation_id` (the auto-approve path
  previously dropped it, which is why the 089 suppression in the UI never
  fired).
- `tests/090-one-tool-row/` — 8 tests: wrapper pairs share a `call_id` on
  success and on handler error; two calls get distinct ids; the executor
  uses the model's call id, also for unknown tools; the projection drops
  arguments/content previews; in-memory decisions carry the conversation;
  a subagent's tool calls are delivered with the **parent** conversation id
  and a paired `call_id` (so they will land in the parent's tool row).

## Verification

- Unit: `tests/090-one-tool-row` 8/8; `tests/049-subagent-capability` and
  `tests/010.1-runtime-swap` green.
- Full core suite: 2335 passed, 67 skipped, **6 failed — identical to the
  baseline recorded in PHASE.md** (test_no_tool_lets_agent_touch_approvals,
  test_plugin_ships_settings_tab_file[plugin_mcp],
  test_missionless_occupies_drive_and_rewrites_onboarding, and three in
  tests/phase04-migrate-files/test_decoupling.py). No regressions.
- Real Luna: booted an isolated `luna serve` on port 8951 (sqlite temp DB,
  dev auth mode, same env as the playwright config) and streamed a turn
  that forces a `load_skill` call. The SSE contained, in order:
  `ui_event {type: tool.called, call_id: bc27f353…, name: load_skill,
  status: pending, conversation_id: <conv>}`, the legacy `tool_call
  {names:[load_skill]}` frame, then `ui_event {type: tool.completed,
  call_id: bc27f353…, status: ok}` — same id on both frames, no
  arguments leaked. The QA instance on :8765 was not touched (it was mid
  luna-fixer turn); it will pick up 0.88.000 on its next restart.
  Probe script: scratchpad `tool_frames_probe.py`.

## Deviations from PHASE.md

- Planned "SSE probe against the QA Luna" became an isolated-instance
  probe (port 8951) because restarting :8765 would have killed a live
  fixer turn. Same code path, same result.
- `_project_tool_frame` takes the event name explicitly instead of reading
  `_event_name` from the payload — the bus only injects `_event_name` for
  wildcard subscribers.

## Surprises / learnings

- **Concurrent session on the same checkout.** While this phase was in
  flight another session committed `0.87.007` (`e6616e1f`, continuation
  prompt fix) from the same working tree, and its stash/pop left
  `luna/agent/runtime.py` with nine `<<<<<<< ours / >>>>>>> theirs`
  conflict blocks. Every block was the same payload with `call_id`/`name`
  in swapped key order (that commit had already absorbed most of the
  call_id edits). Resolved by keeping one side, deleting two duplicated
  `_call_id = uuid4().hex` lines the pop introduced, recompiling, and
  re-running the suites. The other session's still-dirty file
  (`plugin-set.toml`) was left out of the 016 commit. Working on one
  checkout from two sessions is a real hazard — worth an owner decision
  (worktrees) before the next shared-file phase.
- `MockEventBus.captured` records payloads *before* the real bus stamps
  `conversation_id`; tests that care about the stamp must subscribe like a
  consumer would.

## Reassessment of remaining phases

- Phase 2 (UI ToolRow) proceeds as planned. Contract confirmed: the client
  needs no `api.ts` change — `ui_event` frames already reach
  `handleUiEvent`; the row keys on `call_id`, first `tool.called` inserts a
  chip, `tool.completed` flips it to done/error.
- Version: chat-ui in marketplace-src is still 0.19.0, so 2+3 ship together
  as **0.20.0** as planned — unless the other session publishes 0.20.0 first
  (its e2e commit mentions "plugin-chat-ui 0.20.0"); check the marketplace
  before stamping.
- Phase 3 gains one item: drop the 089 `liveAutoIdsRef`/`suppressAutoIds`
  code entirely rather than "fixing" it — with the live row fed by tool
  frames, approvals-fed receipts are only needed for history hydration.
- Phase 4 unchanged.
