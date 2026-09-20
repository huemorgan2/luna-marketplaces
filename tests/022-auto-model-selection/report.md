# 022 — AUTO model selection — execution report

> Current main integration and release versions: [plan 024](../../plan/024-auto-main-integration/PLAN.md). The original branch results below are historical.

Plan: [PLAN.md](../../plan/022-auto-model-selection/PLAN.md). Plugin version: 0.29.3 → 0.30.0. Core companion: Luna plan 110, version 0.93.001.

The real composer now uses core's `selection` field: AUTO sends a mode-only write, concrete models select manual mode, and both survive reload. Missing backend support hides AUTO. The shared core picker renders AUTO first with explanatory copy. Failed saves roll back the optimistic state.

## Results

- Selection integration tests plus existing operations-state regressions: **16 passed**.
- `npm run build`: **passed**, regenerated `ui/chat.js` and `ui/chat.css`.
- [Browser walkthrough](dojo-scenarios.md): **passed** on desktop and mobile. Used installed Playwright with a visible browser because this session has no Playwright MCP. [Machine-readable result](browser-results.json).
- Screenshots: [desktop](shots/auto-desktop.png), [mobile](shots/auto-mobile.png).

The browser used the actual composer and shared core UI with controlled API responses; it did not contact model providers. Two live classifier checks and backend coverage are documented in Luna's companion report.

## Release limits

Source and rebuilt bundle are ready on `codex/022-auto-model-selection`; marketplace publication and production rollout were not performed. Release together with the matching core and server-side Jev key. The existing marketplace worktree's unrelated operations-channel edits were preserved and excluded. The local uninitialized `luna/` directory is a build-only snapshot of the changed core UI, not a submodule update to commit.
