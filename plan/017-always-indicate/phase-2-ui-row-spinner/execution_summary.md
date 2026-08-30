# Phase 2 (+ phase 3 publish) — execution summary

## Shipped

plugin-chat-ui **0.22.0**, commit `76c58d4` on luna-marketplaces `main`
(pushed). Published to marketplace `official`; `index.json` reports
`plugin-chat-ui 0.22.0`. Phase 3 (publish) folded in here — nothing
user-visible ships alone.

Files: `ui-src/src/views/ChatPanel.tsx`, `ui-src/src/__tests__/090-tool-row.test.tsx`,
`ui-src/src/__tests__/012-conversation-isolation.test.tsx`, `luna-plugin.toml`,
`ui-src/package.json`, rebuilt `ui/chat.js` (329.87 kB) and `ui/chat.css`.

What changed for the user:

- The tool row is the only in-turn indicator. Each chip is upserted by
  `call_id`: `pending` → `awaiting` (amber, "· waiting for approval") →
  `running` (shimmer) → `done | skipped | rejected | error`. The server
  hint is shown after the name (`Slow wait · coffee break`,
  `Risky thing · prod`).
- A live indicator (`data-testid="turn-live"`) sits at the end of the row
  for the whole turn: spinner + elapsed `m:ss`, ticking every second. After
  20 s without any frame (tool event, text delta or reasoning delta) it
  turns amber and reads `still working · m:ss`; any frame resets it.
- Error chips are clickable and expand the error text under the row.
- The word "working" no longer appears anywhere; `WorkingLine` is deleted.
  The composer slot shows only the offline notice, and only when not
  streaming.

## Verified

- `npx vitest run`: 17 files, 119 tests pass (5 new 017 tests: no
  working line + indicator inside the row; hint + pending→awaiting→
  running→done upsert; rejected/skipped tags; error click; stall guard with
  fake timers). `npx tsc -b` clean.
- Real browser (Playwright) against an isolated QA Luna on :8951 running
  core 0.88.003 + this build, with a throwaway `plugin_slow` plugin
  (`slow_wait`, `risky_thing` prompt_always). MutationObserver log:
  - `slow_wait(25, "coffee break")`: chip `running` with shimmer from
    t+3 s, indicator `0:03…`, `still working · 0:24` after the silence,
    chip `done`, indicator gone when the turn ended. Screenshot
    `017-mid-tool.png`.
  - `risky_thing("prod")`: `running → awaiting · waiting for approval`
    (amber) with `still working · 0:47` while waiting; after Reject:
    `rejected · rejected`, indicator gone. Screenshot
    `017-awaiting-approval.png`.
  - No "working" text at any point.
- Core suite state recorded in phase 1 (6 pre-existing unrelated failures).

## Deviations from PHASE.md

- Hint preferred keys gained `label` (phase 1 follow-up) after the first
  browser run showed `25` instead of the label.
- Packaging: `package_plugin.py -o <file.zip>` creates a directory of that
  name and writes the zip inside it; publish from
  `<file.zip>/<file.zip>`.

## Reassessment of remaining phases

None left. Plan 017 complete. Open item not in this plan: seven plugin
submodules in luna-plugins are behind origin (listed in chat earlier);
untouched.
