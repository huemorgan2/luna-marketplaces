# 016 — one tool row (chat-ui 0.20.0, core 0.88.x)

Owner feedback (2026-08-30, screenshot of an ops turn): the same turn's tool
activity paints in THREE places — the coalesced receipt row under the bubble
(`AutoToolReceipts`, fed by auto-approval records), the `⟳ 🔧 2 ⌄` progress
pill (`ToolProgressChip`, fed by the chat stream's `tool_call` frames) and
the composer's `working… playbook_list, load_skill` line (`WorkingTools`,
fed by the SAME `tool_call` frames). Findings from the audit:

- Pill and composer line are literally one array rendered twice
  (`onToolCall` → `patchTurn.toolNames` + `logToolCalls`).
- The receipt row is the only surface that sees EVERY tool run (the
  subagent's tools go through the same approval wrapper) — but it has no
  live state: a receipt is stamped when the guard waves the call through
  and never updated.
- 089 meant the pill to replace the receipt row during a live turn; the
  suppression never fires because the auto-approve `approval.decided`
  event (luna/approval/db_impl.py) carries no `conversation_id`.

## Target

ONE surface: a wrap-row of tool chips inside the pending bubble, in the
style of today's receipt row (🔧 + humanised label, repeats coalesce to
`#2 #3`). A chip appears the moment the tool starts, **shimmers** (grey→white
wave, the existing `shimmer-text` class) while it runs, settles to grey when
its result arrives, rose if it errored. Subagent tools appear too. After the
turn the row stays as the receipt; on reload the same row hydrates from
approval records (grey, no shimmer). Composer keeps only spinner + "working…".

## Data

Every tool — main loop and subagent — already passes through the wrapper in
`luna/agent/runtime.py` that emits `tool.called` / `tool.completed` on the
bus; the bus stamps `conversation_id` (088) and the chat SSE already forwards
`ui.*` / `subagent.*` bus events as `ui_event` frames filtered by
conversation (`plugins/plugin_api/app.py::_capture_ui`). Missing: a
`call_id` to pair called→completed, and forwarding `tool.*` the same way.

## Phases

1. **Server tool frames (luna core)** — `call_id` on `tool.called` /
   `tool.completed` (runtime wrapper + tool_executor); forward both onto the
   chat SSE as `ui_event` frames (projected: type, call_id, name, plugin,
   status, error, duration_ms). Also stamp `conversation_id` on the
   auto-approve `approval.decided` emit (fixes 089's dead suppression for any
   client that still relies on it). Tests in `tests/090-one-tool-row/`.
2. **UI ToolRow (plugin_chat_ui)** — per-turn `tools` state keyed by
   call_id, fed from `handleUiEvent`; new `ToolRow` component rendered in
   the pending bubble; shimmer / grey / rose; hydrate from approvals for
   history. Composer: spinner + "working…" only. Tests `090-tool-row`.
3. **Remove duplicates** — delete `ToolProgressChip`, `toolLog`,
   `WorkingTools`, `turn.toolNames`, `liveAutoIdsRef`/`suppressAutoIds`;
   live-turn `AutoToolReceipts` replaced by ToolRow (history path keeps
   using the same component). Retire `089-tool-chip` test.
4. **Publish** — core version bump + push; chat-ui 0.20.0 to the
   marketplace; verify in a real browser against local Luna (:8765).

Phases 2 and 3 are one plugin version (nothing user-visible ships between
them); phase 1 ships core on its own since the UI can't be built without it.

## Constraints

- No new SSE frame type: reuse `ui_event` so `@luna/lib/api` (core UI lib
  the plugin imports) needs no change.
- Do not forward tool `arguments` / result previews to the browser — the
  chip needs name + status only; args can be large and are already visible
  in the debug timeline.
- Row height may grow (wrap) but never one row per call — coalescing stays.

> **Changed by phase 2:** `liveAutoIdsRef` / `suppressAutoIds` are KEPT, not removed — with core 0.88.000 stamping `conversation_id` on `approval.decided` they work and are the "show a tool once live, hydrate receipts from approvals on reload" mechanism. Phase 4 (publish) folded into phase 3; shipped as plugin-chat-ui 0.21.0 (not 0.20.0 — the other session took 0.20.x meanwhile).
