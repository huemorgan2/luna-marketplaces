# 005 — Marketplace experience: test scenarios

## A. Service unit/API suite
1. `uv run pytest` in `service/` — full suite (auth, catalog, publish, reviews,
   handshake, media, bundles, delete).

## B. Handshake protocol (Luna client ↔ service, integration)
2. enroll (new install) → tenant + one-time secret + 8-char link code + link_url.
3. Secret/tenant stored in Luna vault under self-owned `plugin_marketplace.*` names.
4. Signed sync → `{status: ok, installed_count, linked}`; installed list from live registry.
5. Re-enroll → `existing=true`, secret NOT re-issued, fresh code while unlinked.
6. Sync against a never-enrolled marketplace → HandshakeError (no network creds).
7. Corrupted secret → marketplace replies 401 "bad signature".

## C. Browser end-to-end (Playwright, real service)
8. enroll + sync via raw HTTP (as Luna would) → visit `/link?code=…` signed-out →
   sign-up → return to link page → "Link this Luna to my account" → certified.
9. Signed-in certified user opens a plugin detail page → write review (stars +
   title + body) → review appears; histogram/average update.
10. Discover page shows Installed pills for synced plugins and a settings gear
    (deep-link `{luna_base_url}/settings/plugins/{name}`) for plugins with a
    settings tab.
11. Zero JS console errors on Discover, detail, and link pages.

## D. Content
12. All 13 marketplace-src manifests carry a valid taxonomy category; patch
    versions bumped; icon.svg + cover.svg seeded as plugin media (excluded from
    artifacts).
13. Discover renders category kickers, gradient covers, media icons; fallback
    hero cards show a display name (never raw `plugin-*`).

## E. Luna webui
14. `pnpm build` (tsc + vite) clean.
15. `pytest tests/021-marketplace-upgrades tests/008.998-marketplace-default` clean.
16. Marketplaces settings tab: Connect button → link-code panel with link URL;
    Connected badge when linked.
17. `/settings/plugins/{name}` deep-link parses → Plugins tab, card expanded +
    scrolled into view.
