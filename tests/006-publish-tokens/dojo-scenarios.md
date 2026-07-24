# 006 — Publish tokens: dojo scenarios

Server: local service at http://localhost:8600, started with
`GLOBAL_EDITORS=vaselin@gmail.com` and a throwaway sqlite DB.

## Scenario 1 — Owner generates a publish token for their marketplace

1. Sign up / log in as a regular user in the web UI.
2. Create an org + marketplace (or open an existing owned one).
3. Open the marketplace → Settings tab.
4. Publish Token card is visible with a "Generate publish token" button.
5. Click Generate → full `lmp_…` token shown once, with copy button and usage
   snippet (`LUNA_MP_TOKEN=…`, curl example).
6. Reload the Settings tab → only the prefix + created date remain (secret gone).

## Scenario 2 — Token actually publishes

1. Take the token from Scenario 1.
2. `curl -X POST /api/marketplaces/{slug}/upload -H "Authorization: Bearer lmp_…" -F artifact=@hello_world_2.zip`
3. Expect `{"status": "published", …}`.
4. Plugin appears in the marketplace's Plugins tab in the UI.

## Scenario 3 — Scoping and revocation

1. The same token against a different marketplace slug → 403.
2. Click Revoke in Settings → confirm.
3. The curl from Scenario 2 (new version) now returns 401.

## Scenario 4 — vaselin@gmail.com gets a token for the default (official) marketplace

1. Log in as vaselin@gmail.com (locally: email signup; in prod: Google login).
   Server has `GLOBAL_EDITORS=vaselin@gmail.com`.
2. Dashboard shows the `Luna-Marketplace` (official) card under shared marketplaces.
3. Open it → Settings tab → Publish Token card → Generate.
4. Token is issued; publishing to `official` with it succeeds.
