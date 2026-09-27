# Execution summary — 2026-09-27

Status: **code shipped everywhere it can be; fleet migration blocked by Fly billing — owner action required.**
Owner approval: in session 2026-09-27 ("execute it and deploy"; "push to production - if you need to update all luna's do it").

## What shipped

| step | result |
|---|---|
| luna | `fix/messages-window` → main fast-forward `7f5cc2af..93e12422` (2026-09-27 ~13:25Z). Commits `4937b178` (windowed route, cursor paging, page guard, per-row truncation, single-row route, api.ts `messagesPage`/`message`, BasicChat error+Retry, tests/120, version 0.92.060, plans/120) and `93e12422` (review fixes: page guard budgets UTF-8 *bytes* per dialect — `octet_length` on Postgres, `length(CAST(x AS BLOB))` on SQLite; `X-Luna-*` headers CORS-exposed; plan mirror approval line). |
| plugin-chat-ui 0.31.0 | luna-marketplaces main `290bec4..9be2214`: `50c0617` (load-error card + Retry, `messagesPage` with 60 s abort, "Load earlier messages" paging with scroll preservation, resync merge keeps earlier pages, "Show full message" for truncated rows, tests `120-load-window`, stamps 0.31.0), `c5638bc` (review fixes: 404-only deep-link fallback so a 502/timeout on `/chat/<id>` keeps the error card instead of jumping to the newest conversation; stale-load guard; per-conversation load-earlier state; positional resync merge; Copy fetches the full row; `overflow-anchor:none`; plan folder 022 → 027 because 022 already existed twice), `9be2214` (luna gitlink → `93e12422`, gate run WITH build: rebuilt bundle byte-identical). |
| marketplace | `plugin-chat-ui-0.31.0.zip` (5 files, same layout as 0.30.3) published to `official`; index.json shows 0.31.0, sha256 `0df2452e42c9a257a09d5ad770dc807c0c50e1bc424f39b8fba202912f13f5ea`. |
| pin | Render job `job-dashjj0473hc738b3blg`: plugin-chat-ui 0.31.0 pinned into the default plugin set (before build). |
| image | Render job `build --branch main` → LunaImage `0.92.060` (`registry.fly.io/luna-agents:0.92.060`), GH Actions run 36322522726 success 13:30:46Z. Baked set: 20 plugins = fleet 0.92.057 set + plugin-chat-ui 0.31.0 + **luna-service-mobile 0.1.0** (pinned into the defaults by another session at 12:33Z today, luna-marketplaces plan/026; its canary was also blocked by Fly billing). Main also carries the unshipped 118 AUTO model selection and 119 max_tokens ceiling. |
| promote | Render job `job-dashl7jncjis73a6brj0` `promote-preserve --version 0.92.060`: **main image = 0.92.060**, previous 0.92.057 retained, **migrated 0 / 42** — every machine: `Fly 422 {"error":"We require your billing information, please add it at https://fly.io/dashboard/roy-man-968/billing"}`; image cache warm also 422. |
| verify | Render job `job-dashlpfpn0mc738ert2g`: 42 machines on 0.92.057 (21 started, 21 stopped), 18 agents without a machine, `stale: vaselin-scanny-2 0.92.057 started`. |

## Tests

- luna `tests/120-messages-window`: 26 passed (13 cases × SQLite + Postgres). Targeted suites 003/008.95/086/005.923/004/010: 115 passed, 1 skipped. Full suite (excl. 091-dojo-perform, e2e): 2980 passed, 51 skipped, 9 failed + 2 errors — 3 are the known pre-existing failures (007.009 ×2, 113 orphan approval) and 8 are environmental on this Mac: `plugins/plugin_goalseek` is a tracked symlink into luna-plugins, so discovery sees an external plugin-goalseek (missing plugin-scheduler, declares `goal_ratify`/`playbook_*`); all 8 reproduce identically on a pristine `7f5cc2af` worktree (verified independently of the implementing agent). Core UI vitest 148 passed, `npm run build` clean.
- plugin ui-src vitest: 22 files / 157 passed (16 in `120-load-window.test.tsx`, each new test verified to fail on 0.30.3). `node tools/chat-ui-gate.mjs` (with build): 11/11.
- Adversarial review (4 reviewers, 2 lenses per diff): 5 luna findings (1 medium, 4 low) and 9 plugin findings (3 medium, 6 low) — all fixed except the release-ordering one, which was handled by pushing luna first and bumping the gitlink in `9be2214`.

## Not done

- No error groups marked, no tickets replied: the admin API was not reachable this run (no `lsk_` key on this machine); the issue was owner-reported.
- **The fix is not running on any agent yet.** vaselin-scanny-2 (and the other 41 machines) still run 0.92.057 with the unbounded route; conversation `b514b21b-…` still fails to open until the machine is migrated.

## To finish once billing is fixed

1. Add billing information at https://fly.io/dashboard/roy-man-968/billing (Fly app `luna-agents`).
2. `render jobs create srv-d8g5pd42m8qs73ekk2b0 --confirm --start-command "python scripts/rollout_image.py promote-preserve --version 0.92.060"` (idempotent: re-asserts main, migrates every machine, keeps 0.92.057), then `... verify --version 0.92.060` — expect 42 machines on 0.92.060.
3. Open https://luna.com.ai/a/vaselin-scanny-2/chat/b514b21b-aba9-4f0e-903c-bb8459492dba: the newest 200 messages should render within seconds with a "Load earlier messages (N more)" button at the top; a failed load now shows "Couldn't load this conversation." with Retry instead of the empty greeting. The Render request log should show `GET …/messages?limit=200` ≤ 4 MB instead of 11.5 MB / 502.
