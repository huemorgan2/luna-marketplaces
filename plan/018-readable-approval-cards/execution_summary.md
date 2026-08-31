# 018 — execution summary (plugin-chat-ui 0.23.0)

## What shipped

- `luna` submodule pinned to 5df46d9 (core 0.91.000, plan 094) — the
  `@luna` alias now pulls the upgraded shared `InlineApprovalCard` with
  presentation rendering: eyebrow → headline → markdown explanation →
  "View technical change" collapsed diff/text/json blocks. Cards without a
  `presentation` render exactly as before.
- `ChatPanel.tsx` needed one line: the 0.91 core narrows
  `ConversationSummary.kind` to `'building' | 'ops'`, so the conv-state
  SSE patch site now narrows `ev.kind` before spreading it into the row.
  `InlineApprovalCard` props (`reqs`, `decided`, `agentName`) are unchanged
  — no other call-site changes.
- `react-markdown`/`remark-gfm` (new imports in the shared card) were
  already pinned in package.json / tsconfig paths / vite depPins — no new
  dependencies.
- Rebuilt `ui/chat.js` + `ui/chat.css`; version 0.22.0 → 0.23.0
  (luna-plugin.toml + ui-src/package.json; `__version__` derives from the
  toml).

## Verification

- vitest: 119 passed. `tools/chat-ui-gate.mjs`: all checks passed (React
  not inlined).
- Live QA verification of the rendered cards (collapsed + expanded diff,
  legacy cards unchanged) was done against QA Luna during core plan 094
  phase 3 — screenshots live in luna
  `plans/094-readable-approvals/phase-3-moment-state/execution_summary.md`.
  The hosted-tenant E2E happens in luna-plugins master plan 012 phase 4.

## Deviations

None from PLAN.md.
