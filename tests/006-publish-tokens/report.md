# 006 — Publish tokens: execution report

Date: 2026-07-24. Plan: `plan/006-publish-tokens/PLAN.md`. Service version: 0.2.0.

## What shipped

- `publish_tokens` table (`service/app/models/db.py`) — sha256-hashed secrets,
  `lmp_` prefix, one active token per (user, marketplace), revoke flag,
  last-used tracking. Created automatically by boot-time `create_all`.
- `get_current_user` (`service/app/auth.py`) resolves `lmp_` bearers to the
  owning user and pins the token's marketplace scope; session JWTs unchanged.
- Publisher gate (`service/app/routers/plugins.py`, shared by bundles) rejects
  publish-token requests against any other marketplace (403).
- API (`service/app/routers/core.py`): GET/POST/DELETE
  `/api/marketplaces/{slug}/publish-token`. POST revokes the previous token and
  returns the secret exactly once. A publish token cannot mint tokens.
- UI (`service/templates/app.html`): Publish Token card in marketplace Settings
  (visible with edit rights) — generate / one-time reveal + copy / usage
  snippet / regenerate / revoke.
- vaselin@gmail.com: covered by Render env `GLOBAL_EDITORS`, which already
  contained `hue@marketplaces.com.ai,vaselin@gmail.com` — verified in the
  Render dashboard, no change needed.

## pytest (service/tests/test_publish_tokens.py)

`21 passed` (full suite, including 5 new tests): lifecycle + publish with
token, scoping 403, regenerate invalidates old (401) while new works,
revoke → 401, garbage `lmp_` → 401, non-member manage → 403.

## Dojo run (local server :8600, GLOBAL_EDITORS=vaselin@gmail.com, Playwright)

| Scenario | Result |
|---|---|
| 1. Owner generates token in Settings (one-time reveal, prefix after reload) | PASS — `screenshots/006-s1-token-generated.png` |
| 2. curl publish with token → plugin visible in UI | PASS — `{"status":"published","plugin":"dojo-plugins/hello-world-2"}`, `screenshots/006-s2-plugin-published.png` |
| 3. Scope 403 on other marketplace; UI revoke → 401 | PASS |
| 4. vaselin@gmail.com sees official (admin), generates token, publishes to official | PASS — `screenshots/006-s4-vaselin-official-token.png` |

## Deploy

- Commit `b802a83` pushed to `huemorgan2/luna-marketplaces` main.
- Render auto-deploy is off for this service; triggered Manual Deploy →
  `dep-d9hsp43tqb8s73a667i0`.
- Production verification: see below (health version 0.2.0 + publish-token
  endpoint live).
