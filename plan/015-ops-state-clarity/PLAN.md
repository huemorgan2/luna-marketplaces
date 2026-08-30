# 015 — ops "Agent state": say it's about Luna herself (chat-ui 0.19.0)

Owner feedback on 0.18.0 (2026-08-30): out of context, "Diagnose only —
report problems, change nothing" doesn't say WHAT gets fixed. The operations
agent (e.g. the scheduled `luna-fixer-bug-sweep-6h`) maintains Luna herself —
her own playbooks, plugins, connectors, schedules — never the owner's data.
Three changes, ops chats only:

## 1. One-liners name the target

- Identify — "Diagnose Luna's own playbooks and plugins — report, change nothing."
- Fix & wait for approval — "Fix Luna's own playbooks and plugins; each fix waits for your approval."
- Fix & publish — "Fix Luna's own playbooks and plugins, publish without waiting."

Core mirrors the wording in `_STATE_MEANINGS` (luna/agent/runtime.py) so
model and owner read the same contract.

## 2. `Details` expander inside the menu

Header eyebrow gains a `Details ⌄` toggle (right side; ops chats only).
Open: an intro line ("This agent maintains Luna herself — her own playbooks,
plugins, connectors and schedules. Not your data.") and, under each option,
its can/won't bullets (`OPS_STATE_DETAILS` in convState.ts):

- identify — reads playbooks/plugins/connectors/schedules/logs; reports and
  proposes; changes nothing.
- fix_approve — edits them; every fix is an approval card; never publishes.
- fix_publish — edits them; publishes/installs/upgrades right away; waits for
  nobody.

Open/closed remembered per browser (`luna.chat.stateDetails`), default closed.

## 3. Capability-line tooltip = the same details, instantly

The dotted-underline line (`diagnose only — no changes`) keeps its instant
(state-driven, 0 ms) HTML tooltip; it now carries the intro sentence, the
current state's label + one-liner, and that state's can/won't bullets.

## Constraints

- Version 0.19.0; tests updated (Details toggle, persistence, tooltip
  bullets); `npm test` + `npm run build` green; `ui/` shipped.
- The build compiles against the pinned `luna` submodule — sync it
  (`git submodule update --init luna`) before `tsc -b`, or TaskPlanCard fails
  on unrelated types.
