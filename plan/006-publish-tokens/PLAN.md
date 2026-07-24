# 006 — Publish tokens: issue from UI, use for publishing

## Context

Publishing to a marketplace (`POST /api/marketplaces/{slug}/upload`) currently
authenticates only with the user's login JWT (72h expiry, HS256, from
`POST /api/auth/login` or Google OAuth). There is no long-lived credential a
user can copy out of the UI and put into CI / `publish_plugin.sh`
(`LUNA_MP_TOKEN` today must be a short-lived session JWT).

The marketplace Settings tab already shows a *consumer* `access_token` for
private marketplaces (agent-side), but nothing for publishers.

`vaselin@gmail.com` is Roy's Google account. The "default marketplace" is the
seeded `official` marketplace (`service/app/seed_core.py`, slug `official`,
org `luna-official`). It is owned by a synthetic core user; the existing
mechanism for humans to edit it is the `GLOBAL_EDITORS` env allow-list
(`service/app/auth.py`), which also surfaces `official` in the dashboard with
`can_edit=true` (`/api/me/marketplaces`).

## Goals

1. After login, a user can generate a **publish token** for any marketplace
   they can publish to (org role owner/publisher, or global editor), from that
   marketplace's Settings tab in the web UI.
2. The token works as `Authorization: Bearer <token>` on the publish/manage
   APIs (plugin upload, bundle publish), so it drops into
   `publish_plugin.sh` / CI as `LUNA_MP_TOKEN` with no other changes.
3. `vaselin@gmail.com` can log in (Google), see the `official` marketplace on
   the dashboard, open Settings, and generate a publish token for it.

## Non-Goals

- Enforcing the private-marketplace consumer `access_token` on `/mp/` protocol
  routes (unchanged, still open — plan 002 non-goal stands).
- Fine-grained token scopes (read-only, per-plugin), expiry policies, or
  multiple named tokens per user+marketplace. One active token per
  (user, marketplace); regenerate replaces it.
- A `luna-mp publish` CLI command (still ROADMAP §003).
- Org member management UI.

## Approach

### 1. Data model — new table `publish_tokens` (`service/app/models/db.py`)

New table only (no ALTER needed — `create_all` picks it up on boot):

| column | type | notes |
|---|---|---|
| `id` | String PK | uuid |
| `user_id` | FK users.id | who issued it / who it acts as |
| `marketplace_id` | FK marketplaces.id | scope: exactly one marketplace |
| `token_hash` | String, indexed | sha256 hex of the secret; plaintext never stored |
| `token_prefix` | String | first 12 chars, for display ("lmp_ab12cd…") |
| `created_at` / `last_used_at` | Integer | |
| `revoked` | Boolean default false | regenerate revokes the old row |

Token format: `lmp_` + `secrets.token_urlsafe(32)`. Shown in full **once**, at
creation.

### 2. Auth (`service/app/auth.py`, `service/app/routers/plugins.py`, `bundles.py`)

- In `get_current_user`: if the bearer credential starts with `lmp_`, resolve
  via `publish_tokens` (hash lookup, not revoked) → return the owning `User`,
  and stash the token's `marketplace_id` on `request.state.publish_token_mp_id`.
  Otherwise, JWT path unchanged.
- In `_get_marketplace_for_publisher` (plugins.py) and the bundle equivalent:
  if the request authenticated via a publish token, additionally require
  `token.marketplace_id == mp.id` (403 otherwise). Role/global-editor checks
  stay as they are.
- Read-only and browse endpoints are unaffected (they take no auth today).

### 3. API (`service/app/routers/core.py`)

| Route | Behavior |
|---|---|
| `GET /api/marketplaces/{slug}/publish-token` | Metadata for the caller's active token: `{exists, token_prefix, created_at, last_used_at}`. Never returns the secret. |
| `POST /api/marketplaces/{slug}/publish-token` | Revoke caller's existing token for this marketplace, create a new one, return `{token}` (full secret, once). |
| `DELETE /api/marketplaces/{slug}/publish-token` | Revoke. |

Authorization for all three: org role owner/publisher on the marketplace's
org, **or** global editor (mirrors `_get_marketplace_for_publisher`).
Must be JWT-authenticated (a publish token cannot mint publish tokens).

### 4. UI (`service/templates/app.html`)

New "Publish token" card in the marketplace Settings tab (visible when
`can_edit`), below the existing private access-token group:

- State A (no token): explainer line + **Generate publish token** button.
- State B (token exists): shows prefix + created date, **Regenerate** and
  **Revoke** buttons.
- After generate: full token in a readonly monospace input with a **Copy**
  button and a one-time warning ("shown once — store it now"), plus a usage
  snippet: `LUNA_MP_TOKEN=<token> ./scripts/publish_plugin.sh` and the curl
  equivalent.

### 5. vaselin@gmail.com → official marketplace

Use the existing global-editor mechanism, no new code path:

- Render service `srv-d8m7nct8nd3s73dofrm0`: set env
  `GLOBAL_EDITORS=vaselin@gmail.com` (append if the var already has values —
  check first, don't clobber).
- Local dev: same var in the local run environment.
- Result: after Google login as vaselin@gmail.com, `/api/me/marketplaces`
  includes `official` (access "admin", can_edit), Settings tab is available,
  publish-token endpoints authorize via `is_global_editor`.

## Data/API contract

```jsonc
// GET /api/marketplaces/official/publish-token   (Bearer <JWT>)
{ "exists": true, "token_prefix": "lmp_ab12cd34", "created_at": 1753000000, "last_used_at": null }

// POST /api/marketplaces/official/publish-token  (Bearer <JWT>)
{ "token": "lmp_Zk8...44-char-secret" }   // only time the secret appears

// Publishing with it (unchanged endpoint):
// POST /api/marketplaces/official/upload
//   Authorization: Bearer lmp_Zk8...
```

## Risks

- `get_current_user` change touches every authenticated route — the `lmp_`
  prefix branch must fall through cleanly to JWT for normal tokens. Covered by
  running the existing test suite.
- Token is a bearer secret with no expiry: mitigated by hash-at-rest, show-once,
  revoke/regenerate, and marketplace scoping.
- `create_all` on SQLite/Postgres both create the new table; no data migration
  risk since no existing table changes.

## Acceptance criteria

1. Logged-in owner/publisher sees the Publish token card in Settings and can
   generate/copy/regenerate/revoke a token.
2. `curl -H "Authorization: Bearer lmp_..."` upload to the scoped marketplace
   succeeds; same token against a *different* marketplace slug → 403; revoked
   token → 401.
3. Login JWT still works everywhere (existing tests green).
4. vaselin@gmail.com (Google login, with `GLOBAL_EDITORS` set) sees `official`
   on the dashboard and generates a working publish token for it.

## Verification

- `tests/006-publish-tokens/`: pytest covering issue/get/regenerate/revoke,
  scope enforcement, upload with token, JWT fallback.
- Dojo browser run (Playwright MCP): log in, open marketplace Settings,
  generate token, screenshot; then publish a fixture plugin with that token
  via the API and see it appear in the UI. Repeat token generation on
  `official` as vaselin@gmail.com.
- Report in `tests/006-publish-tokens/report.md`.

## Produces version

`service/` has no version constant; noted here per version-bump rules.
Deploy = Render push after approval.
