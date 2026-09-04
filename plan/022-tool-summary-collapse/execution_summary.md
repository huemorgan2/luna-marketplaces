# 022 — execution summary (2026-09-04)

Shipped as **plugin-chat-ui 0.30.0** (commit 9036f8a).

## What landed

- `ToolRow` (ChatPanel.tsx) rewritten: closed summary line — loader (while
  turn runs) · wrench (pulses via new `.shimmer-icon` while a tool is active,
  amber + "waiting for approval" when a call is parked) · call counter ·
  whole-turn timer · chevron. No border; the line itself is the hover/click
  target.
- Open state: per-cluster list (name · hint · status tags · #N markers,
  error rows expandable), `max-h-[100px]` (5 lines) scrolling, auto-pinned to
  the newest call. No per-row spinners or timers — live state is header-only.
- `TurnLive` → `TurnTimer`; new `turnElapsedRef` freezes the final elapsed at
  stream close so the receipt keeps count + time until the next turn.
- List collapses on conversation switch and when the log resets; a turn with
  zero tool calls leaves no receipt (unchanged).

## Verification

- `vitest`: 20 files / 137 tests green (090 suite rewritten to the new
  contract).
- Published to marketplaces.com.ai `official`, catalog `latest_version`
  0.30.0, artifact sha256 `1a808e20…` == local build.
- Upgraded and verified on all 5 running production tenants
  (pluginsdk-test, luna-bug-fixer, linearascent-promote, scanny-2,
  error-log-tracker): `/api/plugins` shows 0.30.0 enabled no-error; served
  `chat.js` carries the `tool-summary` + `shimmer-icon` markers.
  `vaselin-gamer` is stopped — upgrade it on next start.
