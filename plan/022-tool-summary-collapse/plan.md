# 022 — Collapsed tool summary with expandable list

Roy (2026-09-04): the in-turn tool surface becomes a two-state control.

## Closed (default)

One compact line, no border, the whole line is a hover area that toggles open:

```
◌ 🔧 12  1:01   ⌄
```

- **Loader** — spins while the turn is in progress; gone when the turn ends.
- **Wrench** — shimmers (icon pulse) while any tool is actively running; amber
  while a call waits for approval (plus a "waiting for approval" tag — a parked
  turn must be visible without opening the list).
- **Counter** — total tool calls this turn; ticks up on each `tool.called`.
- **Timer** — whole-turn elapsed, ticking while live ("still working" in amber
  after 20 s of silence, as before), frozen at its final value when the turn
  ends. No per-row timers or loaders inside the list — live state lives only
  in this header.
- **Chevron** — right side, opens/closes the list (only shown once there are
  calls to list).

The closed line persists after the turn ends (frozen timer + final count) until
the next user turn, exactly like the old receipt row.

## Open

Header stays on top; below it the full tool list — same content as the old
chip row (humanized name · hint · status tags · #N repeat markers, error rows
rose + click to expand the message under the list) — but **one cluster per
line**, capped at 5 lines (`max-h-[100px]`), scrolling, auto-pinned to the
newest call as frames arrive. No per-row wrench/spinner/timer. Clicking the
header again collapses back.

## Mechanics

- `ToolRow` (ChatPanel.tsx) rewritten: header button (`tool-summary`) +
  collapsible list (`tool-list`); rows keep `tool-chip`/`tool-hint`/
  `tool-error` testids and status colors.
- `TurnLive` → `TurnTimer`: timer only (the spinner moved to the header's
  loader slot). New `turnElapsedRef` freezes elapsed at stream close
  (captured in `setConvStreaming(off)` before `turnStartRef` is deleted;
  cleared when the next turn starts).
- New `.shimmer-icon` CSS (opacity pulse) — `shimmer-text` is background-clip
  and can't paint an SVG; reduced-motion fallback static.
- Open state resets when switching conversations.

## UX guidelines check (vision/ux_guidelines.md)

Bottom line first: closed line IS the bottom line (n tools, elapsed, alive).
One-line bullets with expand-on-demand list; no jargon (humanized names);
calm surface — no border, single hover affordance, no new panels.

## Tests

`090-tool-row.test.tsx` rewritten to the new contract: header
count/loader/timer, open/close, 5-line cap class, auto-scroll pin, chips only
when open, error expand behind open, frozen timer after turn end, stalled
"still working", awaiting surfaces in the header. `012` isolation assertions
unchanged (turn-live still lives in the row while streaming).

## Ship

0.29.3 → 0.30.0 (package.json + luna-plugin.toml; manifest reads the toml),
rebuild ui/, vitest, publish to marketplaces.com.ai, upgrade luna.com.ai
tenant, verify live via CDP browser.
