# 005 — Marketplace Experience (dynamic catalog, certified reviews, Luna handshake)

Thoroughness: thorough
Status: approved for execution (mock approved; Roy: "make the change in the marketplace itself … execute both and test and push to production")

## Context

`mock.html` (approved) shows the target experience: an App-Store-style Discover page
(hero pair → Essentials → large feature cards → Top picks → per-category sections),
category pages, and a plugin detail page with screenshot gallery, agent-access label,
ratings & reviews, and Installed/settings state. Today the real catalog
(`/browse/{mp_slug}`) is a flat grid with emoji icons, no categories, no reviews, no
media, and the service has no way to know a visitor runs Luna.

Two repos change:

- **luna-marketplaces** (this repo) — service DB/API/templates, seed content, deploy to Render.
- **luna** (`../luna`, the real dev repo, current working branch) — `plugin_marketplace`
  gains the handshake + installed-state sync; webui gains a settings deep link.
  Luna-side counterpart plan: `luna/plans/061-marketplace-certified-handshake/PLAN.md`.

Prior exploration established (see AGENTS.md §7 and code refs inline below):

- No Alembic; `init_db()` only does `create_all` — **this plan writes the first startup
  column migration** (`service/app/migrations.py`).
- Plugin metadata is written in three places that must all learn `category`:
  `_ingest_version()` create + update paths (`plugins.py`) and `seed_core._upsert_plugin`.
- Luna has no signing key and no user email; the proven Luna→external-service auth is
  linear-ascent's **enroll → HMAC** pattern (install_id → server-issued secret,
  `X-*-Tenant/Ts/Signature` headers). We reuse it verbatim.
- Public templates have zero HTML escaping — reviews make this a stored-XSS vector;
  escaping is mandatory work in this plan.
- Render auto-deploy is OFF (`luna/plans/008.5-pluginsdk/HOW-TO-DEPLOY.md`); deploys are
  triggered explicitly (Render API/dashboard) after pushing `main`.

## Goals

1. **Dynamic Discover catalog** exactly per mock: curated hero pair, Essentials, two
   large feature cards, Top picks (by rating), one section per category with
   "More {cat} »", category pages, search. Server-rendered data via new
   `/api/catalog/{mp}/discover`; layout/typography/behavior from `mock.html`
   (including responsive rules: 2-col grids <980px, zoom floor 640px).
2. **Category taxonomy + tags** as first-class: `plugins.category` column, validated
   against the 9-category taxonomy in `guidelines.md`; facet + sort + pagination on the
   catalog API.
3. **Ratings & reviews — certified users only.** 1–5 stars, title/body, one review per
   user per plugin, helpful votes, publisher response, histogram summary. **Only
   "certified" users can write reviews: a user becomes certified by linking a real Luna
   install through the handshake below.** Reviews render with a "Certified Luna user" badge.
4. **Luna handshake** (new, both repos):
   - *Enroll*: Luna's plugin_marketplace generates `install_id` (vault-stored), calls
     `POST /api/luna/enroll` → gets `{tenant, secret, link_code, link_url}`.
   - *Link*: owner enters the 8-char code on the marketplace site (signed in) →
     install is bound to the user, user becomes certified.
   - *Sync*: Luna pushes its installed-plugin list (+ which have settings tabs, + its
     base URL when known) via HMAC-signed `POST /api/luna/sync` on install/upgrade/
     delete and on boot.
   - *Web "Installed" state*: signed-in linked users see Installed pills and a gear
     deep-link `{luna_base_url}/settings/plugins/{name}` on the public catalog.
5. **Media gallery**: icons, covers, and screenshots stored content-addressed (reuse
   artifact store), served at `GET /media/{sha256}`, ingested from a `media/` dir +
   `[media]` manifest entries at publish/seed time. First-class on cards, heroes, and
   the detail carousel.
6. **Richer detail page**: agent-access label from manifest data, What's New from
   version history, Information block, More-in-category. No license shown on the
   header (per mock decision).
7. **Ship to production**: Render deploy, official catalog seeded with categories,
   icons, covers, screenshots for the official plugins; live verification.

## Non-Goals

- Paid plugins, editorial CMS, A/B testing, LLM review summaries.
- Protocol version bump — `index.json` changes are additive only
  (`category`, `rating {average,count}`, `icon`); old Luna clients unaffected.
- Redesign of Luna's in-agent marketplace pane (it keeps its own UI; it only gains the
  handshake plumbing + link UI).
- Cryptographic attestation of *who the human is* — certification asserts "this account
  is operated by someone running a real Luna install", nothing stronger.
- Review moderation queue (owner can delete any review; rate limits only).

## Approach — phases

### Phase A — service: schema + migration
1. `service/app/migrations.py` — idempotent startup migrations, called from `init_db()`
   after `create_all`. Dialect-aware: Postgres `ALTER TABLE … ADD COLUMN IF NOT EXISTS`;
   SQLite: inspect `PRAGMA table_info` then `ALTER TABLE ADD COLUMN`. Safe to run
   concurrently/boot-repeatedly.
2. New columns: `plugins.category` (str, indexed, nullable), `plugins.rating_average`
   (float, default 0), `plugins.rating_count` (int, default 0),
   `marketplaces.curation` (JSON, nullable), `users.certified_at` (int, nullable).
3. New tables (create_all handles): `reviews`, `review_votes`, `plugin_media`,
   `luna_installs` (schema in Data/API contract).
4. Category taxonomy constant + legacy mapping (`connectors→connectivity`,
   `global/system→ability` fallback) in one module used by all three write paths.

### Phase B — service: API
1. Catalog: extend `GET /api/catalog/{mp}` with `category`, `sort`
   (`top_rated|most_downloaded|recent|name`), `page`/`per_page` (default 24); response
   gains `category`, `rating_average`, `rating_count`, `icon_url` (media-backed),
   `cover_url`, `screenshots` (list). Keep old callers working (all params optional).
2. `GET /api/catalog/{mp}/discover` — one payload for the Discover page: resolved
   curation (heroes/essentials/features), top picks (highest rated, fallback most
   downloaded), category sections (3 cards + total count each).
3. Reviews: `GET .../{plugin}/reviews` (+`/summary`), `POST/PATCH/DELETE .../reviews`
   (JWT; **403 unless `user.certified_at`**; publisher's own org blocked), `POST
   .../reviews/{id}/helpful`, `POST .../reviews/{id}/response` (publisher). Rating
   denorms recomputed in the same transaction. Rate limit 10 reviews/user/day.
4. Media: `GET /media/{sha256}` (content-type from row, `Cache-Control: public,
   max-age=31536000, immutable`); publisher `POST/DELETE
   /api/marketplaces/{slug}/plugins/{name}/media`; ingest at publish time from zip
   `media/` + manifest `[media]`; seed ingests from `marketplace-src/{pkg}/media/`.
   Storage: reuse content-addressed store with extension-aware paths.
5. Handshake: `POST /api/luna/enroll` (unauthenticated, idempotent per install_id,
   rate-limited 5/hr/IP), `POST /api/luna/sync` (HMAC: `X-Luna-Tenant`, `X-Luna-Ts`,
   `X-Luna-Signature` = HMAC-SHA256(secret, f"{ts}.{raw_body}"), ±300s skew),
   `POST /api/me/link-luna {code}` (JWT), `GET /api/me/luna-installs`,
   `DELETE /api/me/luna-installs/{id}` (unlink; recompute certified),
   `GET /api/me/installed?marketplace={slug}` → installed map + settings map +
   luna base URL for the gear links.
6. Registry index additive fields: `category`, `rating {average,count}`,
   `icon` (`/media/{sha}` absolute path). Purge path (plan 004) extended to delete
   reviews/votes/media rows + orphaned media bytes.

### Phase C — service: web UI
1. `catalog.html` → rebuilt as the Discover page from `mock.html` (same CSS language,
   real data): hero pair, Essentials, feature cards, Top picks, category sections,
   category page mode (`/browse/{mp}?category=X` or client view switch), search
   (server-side), sign-in aware header. Graceful degradation when curation or media is
   missing (text-only hero cards, hash-tint icon fallback).
2. `plugin_detail.html` → rebuilt per mock: gallery carousel (screenshots + cover;
   chat-transcript captions), agent-access label (tools/policies/risk, vault, egress,
   env, depends_on from manifest), description (readme), What's New (latest version +
   history), Ratings & Reviews (summary histogram + reviews + **write/edit form when
   signed in and certified**, "link your Luna to review" hint otherwise), Information
   block, More in {category}.
3. Installed state: if signed in and `GET /api/me/installed` returns data, show
   Installed pills + gear (deep link to the install's Luna settings) on cards, rows,
   and detail header.
4. **Escape all user content** — shared `esc()` in both public templates; reviews,
   readmes (sanitized markdown), names.
5. Account surface on the dashboard (`app.html`): "Linked Lunas" panel — enter link
   code, list installs, unlink. (Minimal; certification is the point.)

### Phase D — Luna: handshake client + deep link (`../luna`, plan 061)
1. `plugin_marketplace` v0.3.0:
   - `handshake.py`: per-marketplace enroll (install_id from vault or new uuid4hex;
     store `install_id/tenant/secret` in vault under
     `plugin_marketplace.mp_{marketplace_id}.*`), HMAC signer, sync payload builder
     (from `_installed_info()` + `/api/ui/plugins` settings knowledge + base URL from
     `LUNA_PUBLIC_URL`/`LUNA_HOST_NAME` env when present).
   - Routes: `POST /api/p/plugin-marketplace/link` (owner) → enroll + return
     `{link_code, link_url}`; `GET /link-status`; sync triggered after
     install/upgrade/delete and on boot (debounced task, failures silent).
   - Pane/settings UI: "Certify with Luna-Marketplace" card showing the code + URL
     (copy button), linked/unlinked status.
2. Webui deep link: `parseRoute()` in `Shell.tsx` learns `/settings/{tab}/{sub}`;
   `PluginsTab` accepts `focusPlugin` → scrolls to/expands that plugin's card.
   Rebuild webui bundle.
3. Unit tests (enroll/sync signing, payload shape) + dojo check.

### Phase E — content (official catalog matches the mock)
1. Add `category` (taxonomy value) to all 13 `marketplace-src` manifests; bump patch
   versions (immutability requires new versions).
2. Per-plugin `media/` dirs: `icon.png` (from `plan/005-marketplace-experience/assets`),
   covers for curated heroes (`assets/covers`), screenshots where they exist; `[media]`
   manifest entries with captions.
3. Additional real plugins from `../luna-plugins` that appear in the mock (giphy,
   image-gen, personality, whatsapp, …): copy into `marketplace-src` **only those that
   package cleanly and exist**; skip the rest — Discover degrades gracefully.
4. Official curation seeded in `seed_core`: heroes (Personality/Image-Gen if present,
   else the two highest-profile plugins), Essentials (web-access, files, playbooks),
   feature cards, top picks auto.
5. Demo reviews **only** in `service/seed.py` (never in the official catalog).

### Phase F — test, deploy, verify
1. Service pytest: reviews lifecycle + certified gate + rate limit, handshake
   enroll/sync/link (incl. bad signature, skew, replay of link code), media serving +
   immutable hash, catalog facets/sort/pagination, discover payload, index
   back-compat, migration idempotency (boot twice), purge extension.
2. `tests/005-marketplace-experience/scenarios.md` + `report.md` with real results.
3. Browser run (Playwright MCP against local service): discover, category, detail,
   review write as certified user, installed pills. Screenshots.
4. Deploy: push `main` → trigger Render deploy → `dojo/verify_live.py` (trust gate) →
   browse production pages → end-to-end handshake from local Luna against prod
   (enroll, link with a real account, sync, certified review).
5. Luna repo: commit on its current working branch (its own plan + execution summary).

## Data / API contract

New tables (`service/app/models/db.py`; String UUID PKs, int epoch timestamps, like the rest):

- `reviews` — id, plugin_id→plugins (indexed), user_id→users, rating int 1–5, title
  str(80), body Text, plugin_version str, helpful_count int=0, created_at, updated_at,
  edited bool=False, response_body Text nullable, response_at int nullable.
  Unique `(plugin_id, user_id)`.
- `review_votes` — id, review_id→reviews, user_id→users, created_at.
  Unique `(review_id, user_id)`.
- `plugin_media` — id, plugin_id→plugins (indexed), kind (`icon|cover|screenshot`),
  sha256 (→ content-addressed bytes), content_type, caption str, sort_order int=0,
  created_at.
- `luna_installs` — id, install_id str unique (client uuid4 hex), secret str
  (server-generated, HMAC key), user_id→users nullable, luna_name, luna_version,
  base_url nullable, installed JSON=list (`[{name, version, settings}]`),
  link_code str nullable (8 chars, uppercase, no 0/O/1/I), link_code_expires int,
  created_at, linked_at int nullable, last_sync_at int nullable.

Columns: `plugins.category` str nullable indexed; `plugins.rating_average` float=0;
`plugins.rating_count` int=0; `marketplaces.curation` JSON nullable; `users.certified_at`
int nullable.

`marketplaces.curation` shape:
```json
{"heroes":   [{"plugin":"plugin-personality","kicker":"Only on Luna","title":"Give your agent a soul","sub":"…"}],
 "essentials": ["plugin-web-access","plugin-files","plugin-playbooks"],
 "features": [{"plugin":"plugin-giphy","kicker":"Plugins we love","title":"…","sub":"…"}]}
```
Hero/feature cover = the plugin's `cover` media. Missing plugin → entry skipped.

Handshake wire contract:

```
POST /api/luna/enroll   {"install_id": "<hex>", "luna_name": "...", "luna_version": "..."}
  → 200 {"tenant": "<id>", "secret": "<token>", "link_code": "ABCD2345",
          "link_url": "https://…/link", "linked": false, "existing": false}
  (idempotent: same install_id → same tenant/secret, fresh link_code if unlinked)

POST /api/luna/sync     headers X-Luna-Tenant, X-Luna-Ts, X-Luna-Signature
  body {"installed": [{"name":"plugin-files","version":"0.9.0","settings":false}],
        "base_url": "http://localhost:8420", "marketplace_slug": "official"}
  → 200 {"linked": true, "username": "roy"}

POST /api/me/link-luna  (JWT)  {"code": "ABCD2345"}
  → 200 {"linked": true, "install": {...}}  ; sets users.certified_at, luna_installs.user_id
GET  /api/me/installed?marketplace=official (JWT)
  → {"installed": {"plugin-files":"0.9.0"}, "settings": {"plugin-files":false},
     "luna_base_url": "http://localhost:8420"}
```

Signature: `HMAC_SHA256(secret, f"{ts}.{raw_body}")` hex; reject skew >300s; constant-time compare. Secrets never logged; enroll rate-limited per IP.

Review certification rule: `POST/PATCH` review requires `users.certified_at IS NOT NULL`;
unlinking the last install clears `certified_at` but existing reviews stay (marked
"certified at time of review").

## Risks

- **First prod column migration** — run on Render Postgres at boot; mitigation:
  idempotent SQL, tested against both dialects, additive-only, no data rewrites.
- **Stored XSS via reviews/readmes** — mandatory `esc()` + sanitized markdown before any
  review renders; test includes a script-tag review.
- **Handshake abuse** — enroll rate limit, link codes expire (15 min) + single-use,
  HMAC replay window 300 s, sync payload size cap (200 plugins).
- **Seed version bumps** — 13 manifests get new patch versions; immutability check makes
  a mistake loud (SKIP log), not corrupting.
- **Purge orphan rows** — plan-004 purge extended for reviews/votes/media; test covers it.
- **Luna webui rebuild** — Shell.tsx change needs the Vite build to succeed in `../luna`;
  if the toolchain fights, deep link ships Luna-side later and the marketplace hides
  gear links when `base_url` is absent (graceful).
- **Scope creep in Phase E step 3** — extra plugins are best-effort; anything that
  doesn't package cleanly is skipped, not fixed.

## Acceptance criteria

1. `/browse/official` renders the mock's Discover: hero pair, Essentials, feature
   cards, Top picks, per-category sections with "More »" only at ≥4 plugins; category
   pages paginate at 24; search server-side; no emojis; fonts never shrink below base
   size (2-col reflow <980px, zoom floor 640px).
2. Detail page: gallery (≥1 item or clean fallback), agent-access label from manifest,
   What's New, histogram summary, reviews list, Information, More-in-category.
3. A signed-in user **cannot** review until they link a Luna install; after
   `enroll → link code → /api/me/link-luna`, the same user can post exactly one
   review per plugin, edit it, delete it; histogram + averages update transactionally;
   publisher (own org) blocked from reviewing own plugin; publisher can respond.
4. A local Luna instance completes enroll+link+sync against the service; the linked
   web user then sees Installed pills and (when base_url known) a working gear link to
   `{luna}/settings/plugins/{name}` which focuses that plugin's card.
5. All official plugins have a category and icon; curated heroes have covers; ≥3
   plugins have screenshots.
6. Old-protocol compatibility: existing `index.json` consumers (Luna 0.2.0 client,
   `dojo/verify_live.py`) pass unchanged; trust gate green in production.
7. Full service pytest suite green; new scenarios documented in
   `tests/005-marketplace-experience/report.md` with real results.
8. Production: deployed on Render, Discover live at
   https://luna-marketplaces.onrender.com/browse/official, trust gate verified live.

## Verification

- `service/tests/`: `test_reviews.py`, `test_handshake.py`, `test_media.py`,
  `test_catalog_facets.py`, `test_migrations.py` + existing suite.
- `tests/005-marketplace-experience/scenarios.md` — API lifecycle scripts + browser
  scenarios; `report.md` with outcomes and screenshots.
- Browser (Playwright MCP): local run pre-deploy, production run post-deploy.
- `dojo/verify_live.py` against prod after deploy.
- Luna side: `luna/plans/061-marketplace-certified-handshake/` PLAN + execution summary;
  pytest for handshake module; live handshake against prod as the final check.
