Status: approved
Approved by the owner in session on 2026-09-27 ("execute it and deploy").

# Unbounded conversation load takes the agent down (GET /api/conversations/{id}/messages)

Reported by the owner on 2026-09-27: `https://luna.com.ai/a/vaselin-scanny-2/chat/b514b21b-aba9-4f0e-903c-bb8459492dba`
spins for a long time and then shows the empty "Hi, I'm Agent" greeting although the conversation is long.

## Evidence

Source: Render request/app logs of luna-service (`srv-d8g5pd42m8qs73ekk2b0`), queried via the Render logs API on
2026-09-27. Admin API error groups and tickets were NOT available (no `lsk_` key on this machine), so there are no
fingerprints or ticket ids in this plan.

**The load itself.** `GET /a/vaselin-scanny-2/api/conversations/b514b21b-…/messages` returns the whole conversation
in one JSON array. Successful loads (status 200) grew with the conversation:

| date (UTC) | bytes | time |
|---|---|---|
| 2026-09-16 11:52 | 59,188 | 0.8 s |
| 2026-09-16/17 (4 loads) | 1,622,377–1,622,974 | 4.3–6.9 s |
| 2026-09-22/24 (9 loads) | 4,812,230–5,173,692 | 11.9–19.4 s (one 43.3 s) |
| 2026-09-24 12:03 | 10,615,833 | 81.4 s |
| 2026-09-24 20:10 – 2026-09-26 16:30 (5 loads) | 10,857,554–10,944,556 | 19.9–68.2 s |
| 2026-09-26 18:22 | 11,518,954 | 72.8 s |

No load has succeeded since 2026-09-26 18:22. Failed loads (status 502, body ~300 B = Fly's edge error) on five
distinct days: 2026-09-23 (1), 09-24 (4), 09-25 (1), 09-26 (5), 09-27 (9). Every attempt since 2026-09-26 19:23 has
failed (9 of 9). 502 latencies are either 2.6–10.8 s or 55–74 s.

**Each failure kills the agent process.** Every one of the 20 failures coincides (±3 s) with luna-service logging
`Proxy stream broke (RemoteProtocolError): https://luna-agents.fly.dev/api/events?…` for the agent's open SSE
streams, and with 502s on unrelated in-flight requests of the same agent, e.g. 2026-09-27 11:09:09Z:
`/api/p/plugin-tasks/`, `/api/p/plugin-identity/`, `/api/p/plugin-marketplace/upgrades`,
`/api/p/plugin-approvals/?status=pending`, `/api/events?topics=*` all 502 in the same second; 11:11:03Z: another
conversation's `/context` 502. Of 124 stream-break events since 2026-09-23, 91 fall within 3 s of one of these 20
failures. After each failure the agent serves nothing for ~50 s (SSE reconnects at 11:10:02, 11:10:58, 11:11:57,
11:13:14 after failures at 11:09:09, 11:10:07, 11:11:03, 11:12:17). Requests to other agents and to
`/proxy/*` kept returning 200 at those instants, so luna-service itself was healthy.

**Recurrence / blast radius.** All 30 `/a/…` 502s since 2026-09-20 belong to `vaselin-scanny-2`; 20 of them are this
GET. No other agent's `/messages` load failed in the window — this is the only conversation of this size, but the
route is the same for every agent.

**What the user sees.** plugin-chat-ui 0.30.3 (`ui/chat.js`, `async function It(x)`) wraps `ae.messages(x)` in
`try { … } catch { return jt(!1), !1 }`: on any failure it turns the spinner off and leaves the message list empty,
so the empty-state greeting (`!Tt && _.length===0 && !a && u.jsx(lb, …)`) renders as if the conversation were new. No
error is shown and there is no retry. The task card on the same page comes from `plugin-tasks` and loads fine, which
is why the page looks "alive" but empty. The core UI has the same swallow at `ui/src/views/BasicChat.tsx:124`.

## Diagnosis (luna origin/main 0d3d2bf1 = 0.92.059; the deployed 0.92.057 has identical code on this path)

- `plugins/plugin_api/app.py:1631-1640` `list_messages`: `select(MessageRow).where(conversation_id == …)
  .order_by(created_at)` → `.scalars().all()` — every row of the conversation, no limit, offset, window or size cap.
- `plugins/plugin_api/app.py:3623` `_msg_to_payload` copies `content`, `tool_calls` and every promoted `extra` field
  (`reasoning`, `tasks`, `attachments`, `embed_html`, …) into `MessagePayload` (`luna/schemas/api.py:165`). Tool
  results are stored as full text rows (`luna/agent/tool_executor.py:113`), so a single file/page dump is one row.
- FastAPI serialises `list[MessagePayload]` in one go, then `GZipMiddleware` (`app.py:1243`) buffers and compresses
  the whole body. Rows, payload objects, the serialised tree, the JSON string and the gzip buffer all coexist.
- `luna/plugins/conversations.py:108` `ConversationReader.messages(conv_id, offset, limit)` (limit clamped 1..100)
  already exists but the HTTP route does not use it.
- luna-service `cloud/api/proxy.py:355-359, 417-421`: for API requests the proxy forwards Fly's edge 502 unchanged
  (no wake/retry — it is a status, not a transport error), so the 502 latencies are Fly's, not luna-service's.
- Machine: `cloud/runtime/fly_machines.py:235-241` `restart.policy=always`, default guest 1 shared vCPU /
  1024 MB (`cloud/provisioning/image_defaults.py:25`). A process that dies is restarted automatically — matching the
  ~50 s outage per attempt.

**Kill mechanism — inferred, not proven.** The only size-dependent way this route can end a process is memory. A
local measurement of the exact serialisation path (fastapi 0.141 / pydantic 2.13, 854 rows, 11.6 MB) peaks at only
+29 MB over the rows and needs < 0.1 s CPU, so the request's own footprint is modest: the process has to be near its
ceiling already for this to be lethal. Consistent with that: the 68–81 s successes on 09-24/25/26 (vs ~20 s
normally), and the 33 other stream breaks since 09-23 cluster at turn starts/ends and approvals (09-25 20:00,
09-27 04:42, 06:41, 09:21). What settles it: the machine's exit events (`fly machine status <id> -a luna-agents`:
`exit_code`, `oom_killed`) or its logs — not reachable from this machine (no Fly CLI/token, no admin key).

## Date validation

- Failing path last changed in 7d7c9d8b (Luna 0.38.005, 2026-07-17, `git log -S'async def list_messages'`); no
  commit since touches `list_messages`/`_msg_to_payload` semantics.
- Running version on the agent: 0.92.057 (owner's screenshot; image built 2026-09-17/18, luna-fixer 3448894 /
  fix18fails). Plan 119's 0.92.059 image was built on 2026-09-25 but NOT applied to this machine (Fly refused the
  update: billing information required — `luna/plans/119-anthropic-output-cap-per-model/execution_summary.md`).
- Events: 2026-09-23 … 2026-09-27, all after the last rollout that touched this machine. plugin-chat-ui 0.30.3 is
  the version served (marketplace artifact inspected 2026-09-27).

## Reproduction

Twenty live occurrences in production logs (above); the code path is proven by reading. Not reproduced
deliberately: each attempt takes the owner's working agent down for ~1 min and kills any running turn.
Controlled reproduction for the fix: a `vaselin-*` throwaway on the default 1024 MB machine, seed one conversation
with ~12 MB of tool-result rows (a few 1 MB rows plus ~800 ordinary ones), load `/messages` while watching
`fly machine status` — expect the same 502 + restart before the fix, a bounded fast response after.

## Proposed change

**A. luna — bound the route (`plugins/plugin_api/app.py` `list_messages`).**
1. Window: `GET /api/conversations/{id}/messages?limit=&before=` returns the newest `limit` rows (default 200,
   max 500) in chronological order; `before=<message id>` pages older rows. Keep the bare-array body so existing
   clients keep working; add `X-Luna-Has-More: 1` and `X-Luna-Next-Before: <id>` headers.
2. Per-row cap for the list view: `content`/`reasoning` longer than 64 KB are truncated with `truncated: true`
   (new optional field on `MessagePayload`); `GET /api/conversations/{id}/messages/{msg_id}` returns the full row
   for the UI's expand/copy actions. Tool-result rows are the ones that reach megabytes; the UI already collapses
   them.
3. Guard: if the windowed payload still exceeds 4 MB, halve the window (log a warning with the conversation id) so
   no single request can approach the machine's memory again.
4. Tests (`tests/` plugin_api): window + cursor order, `has_more`, per-row truncation flag, full-row endpoint,
   guard. Before implementing: grep every consumer of `/messages` (core `ui/src/lib/api.ts:468`, plugin-chat-ui,
   luna-service chat feed added by luna-service migration 0022, dojo tests) and confirm none relies on "all rows".
5. Version bump `luna/__init__.py` to the next free 0.92.x.

**B. plugin-chat-ui (luna-plugins, own repo) 0.30.3 → 0.31.0.**
1. `It()`: on a failed load render an inline error card ("Couldn't load this conversation — Retry") instead of the
   empty greeting; keep the draft; no silent catch.
2. Request `limit=200`; render a "Load earlier messages" control driven by the `X-Luna-Next-Before` header.
3. Give the load an `AbortController` timeout (60 s) so the spinner cannot outlive the edge's 100 s.
4. Mirror the same error state in core `ui/src/views/BasicChat.tsx:124` (small, optional in this plan).

**C. Owner-side ops (not code, needed before any of A/B can reach this machine).**
1. Add billing information on the Fly dashboard: since 2026-09-25 Fly refuses every machine config update on
   `luna-agents` (plan 119), which blocks both the image rollout and a memory resize.
2. Then, optionally, resize `vaselin-scanny-2` to 2048 MB and read its exit events to confirm/refute OOM. Until
   the fix ships: do not reopen that chat — every attempt is a ~1 min outage of the agent.

## Risk and rollout

- Risk: a windowed default changes semantics for any client that expected the full history — mitigated by the
  consumer audit in A.4 and by keeping the array shape. Truncation flag is additive. No DB migration.
- Rollout: publish plugin-chat-ui 0.31.0 → pin into the baked set → push luna → build image → promote → verify
  (CLAUDE.md pipeline). Fleet-wide, since every agent runs the same route. Promotion to this machine is blocked
  until C.1 is done.
- Ledger: no fingerprint (admin API unavailable this run); recorded under `behaviour_issues_2026_09_27` in
  `ledger.json`.
