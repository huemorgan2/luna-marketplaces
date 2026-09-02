# 021 — Identify-only ops chat (chat-ui leg of luna plans/099)

## Context

Luna core 099 makes the ops chat identify-only: one permanent state
('identify'), planning-grade toolset (reads plus file/wiki writes for
findings; every mutating tool absent). Chat-ui 0.25.0 already removed
the ops state picker and capability line when luna 098 collapsed ops to
'building' — but that left the ops chat silently running the FULL
toolset, and the fix-spiral of 2026-09-01 (unapproved publishes,
deleted steps, ignored stop orders) is why 099 reverses course.

Owner ask (2026-09-02): the ops chat only identifies issues; add a
visible box at the top of the chat stating the contract.

## Goals

- The ops vocabulary becomes `identify` alone ('Finds and reports
  issues — fixes happen in a regular chat'); still no picker.
- A standing amber notice under the header: finding issues & live
  activity only — nothing gets fixed here, take the finding to a
  regular chat.
- The notice keys on the RAW server state (`conv.state === 'identify'`,
  not the convState fallback), so a core running ops in any other state
  gets no box and the UI never claims a restraint the server does not
  enforce. Safe to publish before the 099 core deploys.

## Non-Goals

Findings folder UX, per-problem channels, "Fix this" buttons
(discussed, deferred). Building-chat picker untouched.

## Approach

`lib/convState.ts`: STATE_OPTIONS.ops → the one identify entry;
DEFAULT_STATE.ops → 'identify'. `views/ChatPanel.tsx`: new
`OpsIdentifyNotice` rendered right after ChatHeader, gated on
`activeKind === 'ops' && activeConv?.state === 'identify'`.
Tests: 089-ops-state.test.tsx gains a '021 ops identify notice' group
(shows on identify, absent on building chats, absent on a raw non-
identify ops row) and the stray-event sync test also asserts the notice
withdraws. Version 0.28.0.

## Verification

vitest 133/133 (19 files), tsc clean, vite rebuild; packaged and
published 0.28.0 to `official`. The core-side contract is pinned in
luna tests/099-ops-identify.
