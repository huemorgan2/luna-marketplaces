# 008 — Chat UI plugin: execution report (remake on live main)

Date: 2026-07-30. Plan: `plan/008-chat-ui-plugin/PLAN.md`.

> This replaces the earlier report, whose build was made against the stale
> v0.13 lineage and discarded. Everything below was built and verified on
> live `main` (luna v0.53.000, released as 0.54.000 — the final artifact was
> rebuilt after the version bump since the bundle embeds the luna version).

## What shipped

- **`marketplace-src/plugin_chat_ui`** — Luna's full chat experience as a
  plugin. Native runtime mount (NO iframe): `ui/chat.js` is an IIFE built with
  react/react-dom as externals resolved to the shell's `window.LunaReact*`
  globals; it registers `window.LunaChatUI.ChatPanel` and the shell renders it
  inside its own React tree via `replaces_sections=["chat"]`.
  - Sources are NOT vendored: `ui-src` imports shared code straight from the
    live core tree via the `@luna` alias (`luna/ui/src`). Only the chat-only
    views moved into the plugin: `ChatPanel.tsx` (3205 lines), `TaskPlanCard.tsx`,
    `InlineSecretForm.tsx`, plus 5 chat-only unit tests.
  - Artifact: 5 files (`__init__.py`, `luna-plugin.toml`, `routes.py`,
    `ui/chat.js`, `ui/chat.css`), 103,075 bytes, deterministic (packaged
    twice, byte-identical).
    sha256 `14638fcfd6fd071c00aa29f08ec50f9e302a19379e87de405b6fe983b6cdadf3`.
- **Core `BasicChat`** (`luna/ui/src/views/BasicChat.tsx`) — minimal
  always-works chat: streaming text, one-muted-line collapse for anything rich
  (embeds, cards, task plans; approvals/secrets show one line and the decision
  continues the turn), install banner, never a popup. Composer placeholder
  flips to `Luna is working…` while streaming. Roots expose
  `data-testid="basic-chat"` + `data-streaming`; shell wrapper is
  `data-testid="chat-mount"`.
- **Core mount contract** — `main.tsx` exposes `LunaReact` /
  `LunaReactDOM` / `LunaReactJsxRuntime`; `/api/ui/plugins` emits
  `replaces: ["chat"]` + `version` for the claimant
  (`replaceable_builtins = {"chat"}` in plugin_webui/routes.py); Shell's
  `ChatMount`/`RemoteChatPanel` inject `/api/p/plugin-chat-ui/ui/chat.js?v=`
  and degrade to BasicChat on load failure or render crash (ErrorBoundary
  `fallback` prop).
- **Packaging** (`service/app/packaging.py`): artifacts exclude
  `node_modules`, top-level `ui-src/` and `media/` — without this the artifact
  shipped 131 MB of node_modules.
- **Release gate** — `tools/chat-ui-gate.mjs` +
  `.github/workflows/chat-ui-gate.yml` (checks out the luna submodule, runs
  the plugin's vitest too): React major parity, `window.LunaReact*` exposure,
  externals mapping, bundle registers `LunaChatUI.ChatPanel` without inlining
  React. Green locally.
- **Pinned** in `luna/plugin-set.toml`: `plugin-chat-ui 0.1.0`, sha above.

## Verification (all on live main)

- Plugin unit tests: **17/17 pass** (045-first-paint, 045-plan-card,
  045-timeline-memo, 056-card-row, 057-card-action-bridge — moved from core,
  imports rewritten to `@luna/*`).
- Core UI: tsc clean, **98/98 vitest**, production build OK (main bundle down
  to 517 KB with ChatPanel extracted).
- chat-ui-gate: all checks pass; bundle 284.6 KB (88.6 KB gzip), CSS 69.2 KB.
- New e2e spec `26-chat-ui-plugin.spec.ts`:
  - bare core: BasicChat + install banner, registry advertises no takeover —
    **3/3 (+1 skip)**.
  - plugin baked (`LUNA_PLUGIN_SET_DIR`): registry advertises
    `plugin-chat-ui`, rich panel mounts natively (no iframe, no basic-chat),
    composer contract holds (`Message Luna…`, accessible `Send`), and an
    aborted chat.js falls back to BasicChat with no popup or crash card —
    **4/4**.
- Full e2e suite: 18–20 legacy failures in both modes — **all reproduce on
  clean origin/main** (verified with a stashed-tree baseline server): stale
  helpers (sidebar/settings nav became links), approvals API moved to
  `/api/p/plugin-approvals/`, routing/label drift. **The extraction adds zero
  new failures**, and the helper fixes below recovered several specs.
- Perf (5 authed reloads each, no LLM key):
  - rich panel: reload → composer interactive median **327 ms** (222–366);
    chat.js 88.1 KB gzip, browser-cached after first load.
  - BasicChat: median **818 ms** (812–845).
  - Shots: `shots/rich-chat.png`, `shots/basic-chat.png`,
    `shots/26-rich-chat.png`.

## Pre-existing issues found and fixed on the way

- `tests/e2e/playwright.config.ts` hardcoded port 8765 in the webServer
  command while `reuseExistingServer` was on — a dev Luna already listening on
  8765 would be silently reused and its DB mutated by the suite. The port now
  derives from `LUNA_E2E_BASE`; the config also forwards
  `LUNA_PLUGIN_SET_DIR` so the suite can run with baked plugins.
- `_helpers.ts` clicked `button` roles for sidebar/settings nav (they are
  router links since URL routing landed) and asserted a `Luna is working…`
  placeholder the rich panel dropped in 031/043. `gotoSection` /
  `gotoSettingsTab` accept both markups; the streaming assertion accepts both
  panel conventions.

## Known blockers (environment, not code)

- No working Anthropic key: the `luna/.env` key returns **401 invalid** as of
  this run (previously 400 credit-low); the shell-exported key is also 401.
  All LLM-driven e2e stay skipped/failing until a valid funded key lands. The
  chat verification above is LLM-independent.
