# 014 — state selector inside the composer (chat-ui 0.18.0)

Owner feedback on 0.17.0 (2026-08-30): the state pulldown reads as a random
yellow box under the composer; nothing says it sets the AGENT's operating
state. Three changes, both chat kinds:

## 1. Selector moves inside the message box

Bottom-left INSIDE the rounded composer, on the same row as the attach clip +
Send (which stay bottom-right). The row below the box keeps only the model
selector (+ context meter). Applies to building and ops chats.

## 2. Custom dropdown, model-selector style — not a native select

Opens an upward dark panel (same visual family as the model selector menu):

- Header eyebrow: `AGENT STATE` (11px uppercase, faint).
- One row per option: bold short label + ONE dim explanation line
  (ux_guidelines §3 — one-line bullets, no paragraph walls):
  - Planning — "Think and plan — research and notes, no building yet."
  - Building — "Full build — create and change playbooks, schedules, files."
  - Identify — "Diagnose only — report problems, change nothing."
  - Fix & wait for approval — "Prepare fixes; each waits for your approval."
  - Fix & publish — "Fix and publish without waiting."
- Current option highlighted (check or accent border). Click-outside + Esc
  close. Selection PATCHes {"state"} exactly as 0.17.0 (optimistic + refetch
  on failure; never programmatic).

## 3. Chip + ops capability line

- Closed chip: NO amber outline — hairline `--line` border like its
  neighbors; amber text in ops chats. Amber border only while the menu is
  open (or focused). Building chats: neutral text, violet when off-default
  (unchanged from 0.17.0 otherwise).
- Ops chats only: an amber capability line right of the model selector,
  restating what the current state permits:
  - identify → "diagnose only — no changes"
  - fix_approve → "fixes wait for your approval"
  - fix_publish → "fixes publish without approval"
  Hover (visible affordance — the line itself, with a dotted underline)
  shows a dark-panel tooltip, one tight paragraph: this is the Operations
  chat where the agent watches and repairs what you built; the state
  selector in the message box sets what it may do (three states, one line
  each); background reports land here in every state — the state only
  controls what the agent may CHANGE. Tooltip is depth, not the primary
  surface (ux_guidelines §6): the capability line alone must carry the
  bottom line.

## Constraints

- vision/ux_guidelines.md (luna-plugins repo) applied: tokens (`--amber`
  #f5a524, hairline `--line`, chips recolor border+text never fill), no
  jargon, few words.
- Version 0.18.0 (luna-plugin.toml + ui-src/package.json).
- Update 089 tests for the new placement/dropdown; add coverage for the
  capability line + tooltip and the closed-chip border rule.
- npm test + `npm run build` green; ship `ui/` artifacts.
