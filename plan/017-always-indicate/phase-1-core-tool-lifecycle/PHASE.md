# Phase 1 — core tool lifecycle events + hint

## Scope (luna core, luna/agent/runtime.py + plugins/plugin_api/app.py)

1. In the approval wrapper (`_wrapper`, ≈L1873): emit
   `tool.called {call_id, name, arguments, plugin, status: "pending"}`
   immediately after `_call_id` is minted — BEFORE pre-gate, approval,
   policy, vault-ref resolution.
2. Emit `tool.called … status: "awaiting_approval"` right before
   `_approvals.request(...)`.
3. Replace the existing pre-handler emit with
   `tool.called … status: "running"`.
4. `plugins/plugin_api/app.py`: `_tool_hint(arguments) -> str | None` per
   the PLAN rule; `_project_tool_frame` adds `hint` when the payload has
   `arguments`. `_TOOL_FRAME_KEYS` unchanged (arguments still dropped).
5. Tests (tests/090-one-tool-row/test_tool_frames.py, new
   test_lifecycle.py): pending→running ordering on success; pre-gate skip
   yields pending + completed(skipped) sharing call_id; policy=block yields
   pending + completed(rejected); hint rule table (preferred key, secret
   keys skipped, vault ref skipped, truncation, ints, None when nothing).
6. Bump `luna/__init__.py` (0.88.003 if still 0.88.002 at commit time),
   commit, push.

## Verification

- `pytest tests/090-one-tool-row tests/049-subagent-capability -q` green +
  full suite regression count vs baseline recorded in the summary.
- Isolated QA Luna (port 8951 recipe) with the new core: SSE probe
  (scratchpad tool_frames_probe.py) shows `pending` → `running` → `ok`
  frames with a `hint` on a real tool call.

## Out of scope

UI changes (phase 2). No change to tool_executor.py (already emits
pending-before-run and has no gates).
