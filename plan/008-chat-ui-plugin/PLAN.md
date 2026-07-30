# 008 — Chat UI as a plugin (tiny debug chat in core)

## Goal

Extract the rich chat UI out of Luna core into `plugin-chat-ui`, same play as
007 for the marketplace pane. The plugin is the product chat; it is
pre-installed everywhere and effectively core — replaceable in principle,
rarely in practice. Core keeps a **tiny debug chat**: enough to talk to the
agent, ask questions, and understand what's happening. It is not a product and
is allowed to feel bad.

**Guiding principle: save code.** Writing code to degrade *gracefully* is
counterproductive. Basic chat has exactly one fallback behavior — a muted
one-liner — for everything it doesn't render:

> *Not supported in basic chat — check Settings, or install the Chat UI
> plugin.*

If the agent shows an API-key card and the user just sees that line, that's
fine. Losing functionality is fine. Adding core code to soften the loss is
not.

## Current state (verified against the tree)

- Chat is hardcoded React compiled into the core shell bundle:
  `ui/src/views/ChatPanel.tsx` (1517 lines) + `InlineApprovalCard.tsx` (309),
  `InlineSecretForm.tsx` (124), `debug.ts` (330), `approvalGroups.ts` (109).
- Chat is a builtin section (`Shell.tsx:73–76`), always mounted (full-width or
  split rail); plugin sections cannot replace builtins (`Shell.tsx:431,437`).
- The SDK's only rich-content channel is a convention: tool result JSON with
  `embed_iframe`/`embed_html`, sniffed in `plugin_api/app.py:244–254`, stored
  in `MessageRow.extra`. One producer exists (`plugin-charts`,
  `embed_iframe`). Everything else rich rides beside the bubbles: approval
  cards, vault secret forms, playbook styling, `ui_event` frames, debug rows,
  context meter, model picker.
- Skew note: `replaces_sections` and the pane postMessage messages used by
  007's `plugin-marketplace-ui` don't exist in the pinned luna submodule yet.

## Design

### Basic chat (core) — the debug layer

One small component (target ~200 lines, frozen once landed):

- conversation list: names only, click to open, one New chat button — no
  rename/delete/copy;
- messages: user text, assistant markdown (`ReactMarkdown` + gfm), streaming
  deltas, pending dots;
- composer: textarea, Enter to send, error state;
- **the muted line** for everything else, one shared element, static text:
  - message carrying `embed_iframe`/`embed_html` → muted line (charts etc.);
  - `approval.requested` / `vault.secret_requested` SSE event → muted line
    (the Settings → Approvals / Vault panels already exist in core and do the
    actual work — zero new code there);
  - anything future/unknown → same line or silently ignored, whichever is
    less code;
- if `plugin-chat-ui` is not installed: a plain banner with a one-click
  install via the existing install API (no chicken-and-egg);
- playbook-sourced messages render as plain text — no styling, no footer.

Kept for the e2e suite (free — just names): `div.prose-luna`, "Message Luna…"
placeholder, `Send` / `New chat` labels.

Explicitly **not** in basic chat: approval cards, secret forms, embeds, debug
mode (Cmd+D), transcript copy, context meter, model picker (Settings → Models
still works), playbook styling, `ui_event` handling beyond what the shell
already does.

### No SDK / backend changes

- Backend chat surface is untouched: `/api/conversations*`,
  `chat_event_stream`, `/api/events`, slash commands, `send_chat_message`,
  onboarding, the embed sniffer — all as-is.
- No message-kind enum, no `chat_features` negotiation, no new `luna_sdk`
  exports, no install-time compat gates. Plugins keep emitting what they
  emit; basic chat just doesn't render it. A degradation *contract* is code
  we don't need.

### `plugin-chat-ui` — the product chat

- `marketplace-src/plugin_chat_ui`; the current `ChatPanel.tsx` + its four
  sibling files move in **wholesale** — approval cards, secret forms, embeds,
  debug mode, context meter, model picker, playbook styling, transcript
  copy. No rewrite; self-contained bundle served at
  `/api/p/plugin-chat-ui/ui/` (pattern: `plugin_marketplace_ui/routes.py`).
- Pre-installed: seeded at provisioning and re-seeded on boot if missing
  (same mechanism as seeding the official source). Basic chat only ever
  appears when the plugin is missing, disabled, or being debugged — which is
  its entire job.
- Ships UI changes through the marketplace: no Luna bump, no tenant rebuild.

### Handover — native ESM module, NOT an iframe

Chat is the primary surface; an iframe would degrade it (focus/scroll/shortcut
quirks, a postMessage bridge for every shell interaction, double React). The
plugin instead ships a **compiled ESM bundle the shell imports at runtime**:

- plugin serves `/api/p/plugin-chat-ui/ui/chat.js` — the existing
  `ChatPanel` built with `react`/`react-dom` as externals (shell exposes its
  copy; one React instance);
- shell: plugin installed + enabled → `await import(url)` and render the
  component exactly where `<ChatPanel>` mounts today — same props
  (`onUiEvent`, token, split-rail width), same React tree, pixel-identical
  UX. Wrapped in an ErrorBoundary: module fails to load or throws → basic
  chat. That's the whole fallback — no health probe, no postMessage
  protocol, no ready-handshake;
- if the rich chat misbehaves, disabling the plugin returns basic chat.

Accepted coupling: the plugin builds against the shell's exposed React
version. Fine for an effectively-core, pre-installed plugin with basic chat
as the fallback. Plugin JS runs unsandboxed in the shell page — not a
regression; plugins already run arbitrary Python in the server process.

What can break the coupling, and the rule that contains it:

- breakers: a React **major** upgrade in core (or a `react/jsx-runtime`
  change), renaming the React exposure (import map / global), changing the
  chat component's props or SSE/REST contract, renaming `--color-luna-*`
  theme tokens;
- **release gate**: Luna CI builds `plugin-chat-ui` against the new core and
  mounts it as part of the release checks. A Luna release that bumps React
  major or touches the exposed contract must republish `plugin-chat-ui` in
  the same breath;
- failure is soft either way: module fails to load or throws → basic chat;
  a fixed plugin publishes through the marketplace in minutes, no Luna
  release. The broken window is upgrade→republish, and basic chat covers it.

## Pros

1. **Big net code deletion from core** — ~2300 lines of rich chat UI leave
   the core bundle; basic chat is ~200.
2. **Ship chat UI in minutes, not releases** — chat iterates through the
   marketplace like the marketplace pane does.
3. **Core stops rendering raw HTML** (`dangerouslySetInnerHTML` and the
   embed iframe leave core entirely).
4. **One fallback rule** — nothing per-kind to maintain, nothing to keep in
   sync as plugins invent new content.
5. Chat becomes overridable in principle (skins, experiments) without
   touching core.

## Cons / accepted losses

1. Basic chat is deliberately poor: approvals and secret requests are a
   muted pointer to Settings; charts are a muted line; no debug mode, no
   context meter. **Accepted — it's a debug layer, not a product.**
2. A pending approval mid-turn requires a trip to Settings → Approvals.
   Accepted.
3. Fresh instances depend on seeding for good UX. If seeding fails, users
   see basic chat + install banner — which still works.
4. Self-upgrade of the chat plugin while its own UI is mounted needs a
   reload nudge (same one-liner 007 uses).
5. Version skew plugin ↔ core APIs has no gate; the plugin targets current
   core APIs and the fallback is basic chat. Accepted.

## Phases

1. **Takeover support** (luna, shared with 007's skew work): land
   `replaces_sections` incl. builtin takeover + the runtime ESM mount
   (expose React, dynamic `import()`, ErrorBoundary fallback).
2. **Split** (luna): write `BasicChat` (~200 lines incl. muted line +
   install banner); delete `ChatPanel.tsx` and siblings from core; shell
   mounts plugin iframe or `BasicChat`. Luna minor bump.
3. **Plugin** (marketplace-src): scaffold `plugin_chat_ui`, move the rich
   chat in wholesale, publish to official with moonlit icon/cover.
4. **Seeding**: pre-install at provisioning + boot-if-missing (luna-service
   + local boot path).

## Tests

- Dojo: fresh (seeded) Luna → rich chat works as today.
- Disable `plugin-chat-ui` → basic chat: send, stream, switch conversations,
  onboarding kickoff; charts tool → muted line; approval flow → muted line
  in chat, decidable in Settings → Approvals, turn continues after decision.
- Install banner appears when plugin missing; one click → rich chat after
  reload.
- e2e suite passes against rich chat (default); smoke spec for basic chat
  using the same selectors.

## Non-goals

- No backend or SDK changes of any kind.
- No per-kind degradation, feature negotiation, or compat gating.
- No approval/vault UI in chat when degraded — Settings panels are the UI.
- No new framework; the plugin ships the existing React chat unmodified.
- No sanitizer work; channels (Slack/Telegram/voice) untouched.
