# Phase 1 — server tool frames (luna core)

## Baseline (before any change, 2026-08-30)

- luna core `uv run pytest tests/ -m "not integration and not qa"`: see
  execution_summary (run started before edits; result recorded there).
- plugin_chat_ui `vitest run`: 17 files, 112 tests, all passing.
- Local QA Luna on http://localhost:8765 (`/api/health` ok), core 0.87.006.

## Scope

1. `luna/agent/runtime.py` tool wrapper: generate `_call_id = uuid4().hex`
   per invocation; add `"call_id"` to every `tool.called` / `tool.completed`
   emit inside the wrapper (including the pre-gate / rejected / blocked /
   timeout / error paths so a chip can still settle when the body never ran).
2. `luna/agent/tool_executor.py`: same, using the model's `call.id`.
3. `plugins/plugin_api/app.py` `chat_event_stream`: subscribe
   `tool.called` + `tool.completed` with a projecting capture
   (type, call_id, name, plugin, status, error, duration_ms,
   conversation_id) and the existing cross-conversation filter; forwarded as
   `ui_event` frames like `subagent.*`.
4. `luna/approval/db_impl.py` + `in_memory_impl.py`: auto-approve
   `approval.decided` carries `conversation_id`.

## Deliverables

- Code above; `tests/090-one-tool-row/` with: wrapper emits a
  called/completed pair sharing one call_id (ok + error path); executor
  pair uses call.id; `_capture_tool` projection drops arguments and drops
  frames from other conversations; decided event carries conversation_id.
- Core version 0.88.000, committed + pushed; real-Luna check: send a turn on
  :8765 that calls a tool and observe `ui_event` frames
  `tool.called`/`tool.completed` with matching call_id in the SSE.

## Verification criteria

- Full unit suite passes (no regressions vs baseline).
- SSE probe shows the pair for a main-loop tool AND for a subagent tool
  (same conversation_id as the parent turn).
