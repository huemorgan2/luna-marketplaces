# Phase 3 — remove the duplicate surfaces (+ phase 4 publish folded in)

## What shipped

**plugin-chat-ui 0.21.0** — commit `07ddd16` in luna-marketplaces (pushed),
published to the `official` marketplace (`{"status": "published",
"version": "0.21.0"}`; `index.json` confirms). Runs against Luna core
**0.88.000** (phase 1, commit `457c4ea9`); on older cores the row stays
empty and the composer still shows "working…".

Removed from `ui-src/src/views/ChatPanel.tsx`:

- `ToolProgressChip` (the `⟳ 🔧 n` pill) and its detail panel.
- `TurnUI.toolNames`, `EMPTY_TURN.toolNames`, every `onToolCall` name feed
  (`send`, `attachTurnStream` resume, onboarding stream), the `toolNames`
  Composer prop; `WorkingTools` (spinner + tool names) is now `WorkingLine`
  (spinner + "working…" only).
- Test `089-tool-chip.test.tsx` retired.

Kept (changed vs. PLAN.md, decided in phase 2): the 089 live-receipt
suppression (`liveAutoIdsRef` / `suppressAutoIds`). With core 0.88.000
stamping `conversation_id` on `approval.decided` it now works, so a tool
appears once in the live turn (ToolRow) and history after a reload comes
from the approvals-fed `AutoToolReceipts` rows.

Version stamps: `luna-plugin.toml` 0.21.0, `ui-src/package.json` 0.21.0
(this plugin has no pyproject.toml). Bundle `ui/chat.js` + `ui/chat.css`
rebuilt and committed.

## Verification

- `tsc -b && vite build` clean.
- vitest: 17 files, **115 passed, 0 failed** (baseline 112; +7 new in
  `090-tool-row`, −4 retired 089 tests).
- Real Luna, real browser: isolated `luna serve` on :8951 (core 0.88.000,
  sqlite temp DB, `LUNA_PLUGIN_SET_DIR` pointing at this checkout so the
  0.21.0 build loaded — `plugin.loaded name=plugin-chat-ui version=0.21.0`),
  playwright driving the UI. Sent "call load_skill twice…". A DOM observer
  recorded the row's states in order:
  1. composer `working…`, no chips
  2. chip `Load skill` status=running **with `.shimmer-text`**
  3. chip `Load skill` status=done, no shimmer (grey)
  4. chip `Load skill #2` (second call clustered as a tag), done
  5. turn ends: `working…` gone, row still there, grey
  Throughout: `tool-progress-chip` never present, `auto-tool-receipts`
  count 0. Screenshots: `016-mid-turn.png`, `016-after-turn.png` in this
  folder (the after-turn one shows the single grey "Load skill #2" line
  under the reply and a clean composer).
- Reload of that conversation: no row, no receipts — expected for
  `load_skill`, a built-in that never creates an approval record (same as
  before this plan; plugin tools with auto-approve records do hydrate,
  covered by the unchanged `AutoToolReceipts` path and the 090 unit test).

## Deviations from PHASE.md

- Phase 4 (publish) folded in here since build + browser check were green.
- Packaging: `package_plugin.py` on the raw plugin dir swallowed
  `ui-src/node_modules` (44 MB, 10k files; the upload timed out). Packaged
  from an `rsync` copy excluding `ui-src` and `__pycache__` → 6 files,
  258 KB. Worth teaching the script an exclude list.

## Surprises / learnings

- The other session published 0.20.0 and 0.20.1 of this plugin while the
  plan ran; the version target moved to 0.21.0 accordingly.
- `MutationObserver` + `data-status` on chips made "did it shimmer before it
  went grey" checkable without screenshots timing luck.

## Reassessment of remaining phases

- Phase 4 is done (folded in). Plan 016 is complete.
- Follow-ups, not started: (a) built-in tools (`load_skill`, `load_tools`)
  leave no history receipt after reload — if that matters, persist
  tool.completed frames on the message or emit an approval-style record;
  (b) the QA Luna on :8765 still runs core 0.87.007 + chat-ui 0.20.1 until
  its next restart — it was mid-turn and left alone; (c) `package_plugin.py`
  exclude list.
