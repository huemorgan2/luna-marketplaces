# 007 — Marketplace UI as a plugin (degradable core pane)

## Goal

Extract the rich Marketplace pane out of Luna core into an installable plugin
(`plugin-marketplace-ui`) so the UI can be iterated and shipped through the
marketplace itself, without Luna releases. Core keeps a minimal, always-working
**degraded pane** — search + paginated list + install — that upsells the full
UI plugin when it is not installed.

## Current state

- `luna/plugins/plugin_marketplace` (core, SYSTEM app) contains **both**:
  - backend: source management, catalog merge, install/upgrade APIs, agent
    tools, Settings → Marketplaces tab;
  - UI: `ui/{index.html,app.js,style.css}` — a hand-kept duplicate of the
    hosted marketplace design (heroes, categories, detail, reviews).
- Every design change ships twice (hosted site + pane) and requires a Luna
  version bump + luna-service submodule bump + deploy.

## Design

### Core keeps (always works, never degrades further)

- All backend routes, agent tools, settings tab. Installing plugins must never
  depend on an installable plugin.
- **Degraded pane** replacing the rich SPA:
  - search box (client-side over the merged catalog);
  - paginated flat list, 24/page: icon, name, version, 2-line description,
    Install / Update / Installed + settings gear (existing testids kept);
  - error/empty states;
  - a persistent banner: “Get the full Marketplace experience” with a
    one-click **Install** that uses the existing install API to install
    `plugin-marketplace-ui`. No chicken-and-egg: the degraded list can always
    install the UI plugin.
- This is roughly the existing no-rich-data fallback list in `app.js`; the
  rich SPA code is deleted from core.

### New plugin: `plugin-marketplace-ui`

- Published to the official marketplace like any other plugin.
- Declares `sidebar_sections=[SidebarSection(id="marketplace", …)]` and ships
  the current rich SPA via plugin-owned webui (precedent:
  `005.924-plugin-owned-webui`, `005.909-plugin-ui-slots`).
- Talks to the same core plugin_marketplace APIs the pane uses today
  (TOKEN/BASE plumbing unchanged) plus the cross-origin `/mp/{slug}` rich
  fetches.

### Handover mechanism (choose A)

- **A (recommended): sidebar takeover.** Core checks at pane-registration
  time whether `plugin-marketplace-ui` is installed and enabled; if yes, core
  does not render its section (the plugin's section, same id/label/sort_order,
  appears instead). One `if`, no new framework.
- B (rejected): core pane iframes the plugin's UI when present — extra
  indirection, same result, worse debugging.

### Single-source option (recommended follow-up)

Build the plugin's UI from the `luna-marketplaces` repo — the same source that
serves the hosted `/browse` page — and publish the plugin from CI there. This
ends the hand-kept duplication: one codebase ships the website and the
in-Luna UI.

## Pros

1. **Ship UI in minutes, not releases.** Marketplace UI changes publish
   through the marketplace; no Luna bump, no submodule bump, no Render deploy
   of luna-service.
2. **Kills the duplicate.** With the single-source build, hosted site and
   pane come from one codebase (today they are hand-synced).
3. **Smaller core.** The system app shrinks to backend + a tiny list; a bug
   in the rich UI can never take away the ability to install plugins.
4. **Dogfooding.** The marketplace distributes its own UI — proves plugin
   webui is first-class, and its install/update flow gets exercised by every
   user.
5. **Opens the door to alternative UIs** (skins, experiments, A/B) without
   touching core.

## Cons / risks

1. **Two UIs exist** (degraded + rich). Mitigation: degraded is deliberately
   tiny and frozen — search + list only.
2. **Version skew** between plugin UI and core APIs. Mitigation: plugin
   declares a minimum core API version (`sdk_compat`/manifest `requires`);
   core rejects incompatible installs; degraded pane is the fallback.
3. **Fresh-install UX regresses one step**: first thing a new user sees is
   the plain list + banner. Mitigation: pre-install `plugin-marketplace-ui`
   at provisioning (luna-service), same as seeding the official source.
4. **Self-update edge case**: the UI plugin upgrading itself while its own
   iframe is open — needs a post-upgrade reload notice (banner + reload
   button).
5. **If the marketplace is unreachable**, the UI plugin can't be fetched —
   degraded pane must remain fully functional forever (it is the permanent
   safety net, not a temporary shim).
6. **Support surface**: “which UI was active?” becomes a debugging question.
   Mitigation: pane footer shows `core-list` vs `plugin-ui vX.Y`.

## Phases

1. **Split** (luna): carve the degraded list + banner out of `app.js`; delete
   the rich SPA from core; sidebar-takeover check. Luna minor bump.
2. **Plugin** (luna-plugins or marketplace-src): scaffold
   `plugin-marketplace-ui`, move the rich SPA in, wire webui + sidebar
   section. Publish to official; add moonlit icon + cover.
3. **Handover polish**: banner install flow, post-install/uninstall reload,
   self-upgrade reload notice.
4. **Provisioning**: pre-install the plugin on new instances (luna-service).
5. **Single-source** (optional, later): move the plugin build into
   luna-marketplaces CI so site + plugin share one UI source.

## Tests

- Dojo: fresh Luna → degraded pane with banner; banner Install → rich pane
  after reload; disable/uninstall plugin → degraded pane returns; upgrade
  available → Update button works in both panes.
- API-version skew: plugin requiring a newer core is refused with a clear
  message; degraded pane unaffected.
- Existing `mp-*` testids preserved in both panes so dojo suites run against
  either.

## Non-goals

- No change to the hosted website.
- No new UI framework; the plugin ships the same vanilla JS SPA.
- No removal of core install/upgrade APIs from the system app.
