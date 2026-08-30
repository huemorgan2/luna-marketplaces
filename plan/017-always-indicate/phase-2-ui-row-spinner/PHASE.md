# Phase 2 — chat-ui 0.22.0: row upsert + hint, end-of-row spinner, no "working"

## Scope (marketplace-src/plugin_chat_ui/ui-src/src/views/ChatPanel.tsx)

1. `ToolLogEntry.status`: `pending | running | awaiting | done | skipped |
   rejected | error`, plus `hint?`. `logToolCalled` becomes an UPSERT by
   call_id (status from the frame: pending/running/awaiting_approval→awaiting;
   hint kept once seen). `logToolCompleted`: ok→done, skipped→skipped,
   rejected→rejected, else error (error text = error ?? reason).
2. Chip label = `humanizeTool(name)` + ` · hint` (hint in a dimmer span).
   pending/running → shimmer; awaiting → amber static `· waiting for approval`;
   skipped/rejected → grey with `· skipped` / `· rejected`; error → rose.
   Clicking an error chip toggles its error text inline under the row (D2).
3. End-of-row live indicator (`data-testid="turn-live"`): while the turn
   streams, `⟳ 0:42` (elapsed since the turn started, 1 s ticker). After
   20 s with no frame (delta / reasoning / any ui_event / tool frame) the text
   becomes `still working · 1:40` in amber (D3). Rendered even when the row
   has no chips. Gone when the stream closes.
4. Composer: `WorkingLine` deleted; the slot shows nothing while streaming
   (only the offline notice remains).
5. Activity bookkeeping: `turnStartRef`/`lastActivityRef` maps keyed by
   conversation, set in `setConvStreaming(on)`, bumped in `queueDelta`,
   `queueReasoning`, `handleUiEvent`.
6. Tests: 090-tool-row.test.tsx rewritten for the new statuses/hint/spinner
   (fake timers for the 20 s guard); 012 test adapted (no working-line).
7. `tsc -b && vite build`, version 0.22.0 (luna-plugin.toml + package.json),
   commit ui/chat.js + ui/chat.css.

## Verification

- `npx vitest run` green.
- Isolated QA Luna (:8951 recipe) running phase-1 core with this plugin:
  playwright observer logs chip statuses (pending→running→done with hint),
  the live indicator ticks, "still working" appears after 20 s of silence
  (forced with a slow tool or a paused model), composer shows no text.
  Screenshots into this folder.
