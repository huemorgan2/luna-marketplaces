# 009 — Fleet rollout fix + marketplace trust cleanup

Five workstreams. **A** is an investigation/repair of the stalled 0.52.000
rollout; **B–E** are product changes to the marketplace surfaces.

| # | Work | Surfaces touched |
|---|------|------------------|
| A | Finish the 0.52.000 fleet rollout, fix the tooling that hid its failure | luna-service (control plane), Fly fleet |
| B | Remove License everywhere | luna SDK, luna-mp, marketplace service + UIs, plugin manifests |
| C | Write reviews from inside Luna | marketplace service, Luna core, plugin-marketplace-ui |
| D | Remove all git URLs from plugins | marketplace service + UIs, plugin manifests/readmes, prod data |
| E | No fake zero-star state | plugin-marketplace-ui, hosted detail + catalog |
| F | Update banner lists what it will update | plugin-marketplace-ui |

---

## A. Investigate and finish the 0.52.000 rollout

### What is actually true right now

- Image `registry.fly.io/luna-agents:0.52.000` exists, pulls, and boots (a Fly
  cache-warm on it succeeded).
- A `luna_images` row for it exists with `build_status='built'` — **and so do
  ~3 duplicates**; there are ~5 image rows in total.
- No row for 0.52.000 has `is_main`; **0 agents** are on 0.52.000. Tenants are
  a mix of 0.48.007 / 0.50.000 / 0.51.001.
- The earlier "fleet migrated" report was wrong. Root cause below.

### Root causes to fix (tooling)

1. **`promote_main` raised `TypeError`.** The route is declared
   `promote_main(image_id: str, …)` and calls `uuid.UUID(image_id)` internally
   ([admin_routes.py:1363-1371](../../../luna-service/cloud/api/admin_routes.py#L1363));
   the job passed a `uuid.UUID` object. Nothing was written.
2. **The verification oracle was invalid.** Render one-off jobs report
   `succeeded` for *every* exit code and for uncaught exceptions, and the
   quoted `python -c "…"` start-command form silently never executes. All
   conclusions drawn from job status were meaningless.
3. **Duration side-channel is unreliable** — baseline container time varies by
   tens of seconds, which is what produced contradictory diagnoses.

### Fix

- **A1 — real output channel.** Use the Render REST logs API with the CLI's
  stored key (`~/.render/cli.yaml` → `api.key`):
  `GET /v1/logs?ownerId=<workspace>&resource=<srv>&startTime=…`, filtered by
  the job's `instance` label. Verified working. Retire duration encoding.
  Fallback if job instances turn out to be filtered out: add this machine's IP
  to the `luna-service-cp` allowlist for the duration of the work and use
  `render psql … --command`, then restore the allowlist.
- **A2 — diagnose the failed DB-only promote.** A job that set `is_main=True`
  on the 0.52.000 row reported success, yet `is_main` is still false. With A1
  in place, re-run it and read the actual traceback/commit result. Prime
  suspects: writing the wrong duplicate row, or the ORM update being
  overwritten by the bulk `UPDATE luna_images SET is_main=false` in the same
  transaction.
- **A3 — dedupe the image rows.** Keep the single 0.52.000 row whose
  `registry_tag`/`git_sha` match the built image; delete the rest. Do it in one
  transaction with the row ids printed before and after.
- **A4 — promote properly.** Call the real route with `str(image_id)` so the
  audit trail, `_migrate_all_agents`, old-main cleanup and cache-warm all run
  exactly as the admin UI would.
- **A5 — verify from outside.** Sample `https://luna-agents.fly.dev/api/version`
  ~20× and require 0.52.000 on every hit; cross-check the per-agent
  `image_version` count in the DB. Do not report success on any weaker signal.
- **A6 — fix the trap for next time.** Two options, pick during execution:
  make `promote_main` accept `uuid.UUID | str`, or add a small
  `cloud/scripts/promote_image.py` entrypoint that takes a version string and
  is the supported headless path. Prefer the script — it is what any future
  rollout should call.
- **A7 — correct the memory file** `luna-service-deploy-surfaces.md`, which
  currently documents the *broken* quoted-command form and the bogus
  exit-code/status oracle.

**Exit criteria:** every tenant machine reports 0.52.000; one `luna_images` row
per version; a documented, repeatable headless promote path.

---

## B. Remove License everywhere

License is currently a manifest field, a DB column, an API field, a filter, an
edit-form input, and an Information row in both detail UIs.

- **Manifest / SDK** — drop `license` and `license_terms_url` from
  `PluginManifest` ([luna/plugins/base.py:138](../../luna/luna/plugins/base.py#L138))
  and from `luna_mp/schemas.py:86,130` + `build.py:188`. Pydantic's default
  `extra='ignore'` means already-published plugins that still declare
  `license=` keep loading — no forced re-publish.
- **Plugin sources** — delete `license=` from every manifest in
  `marketplace-src/*/__init__.py`, `luna/plugins/*/__init__.py`, and any
  `luna-plugin.toml`.
- **Marketplace service** — drop `Plugin.license` from
  [db.py:120](../service/app/models/db.py#L120), the three schema fields
  ([schemas.py:109,147,235](../service/app/models/schemas.py#L109)), the
  response mapping and the `?license=` filter
  ([plugins.py:45,199,263,288](../service/app/routers/plugins.py#L45)), and the
  seed defaults (`seed.py:229`, `seed_core.py:214`).
- **UIs** — remove the `['License', …]` Information row from
  [plugin_detail.html:334](../service/templates/plugin_detail.html#L334) and
  [plugin-marketplace-ui app.js:724](../marketplace-src/plugin_marketplace_ui/ui/app.js#L724),
  and the License input + `<dt>License</dt>` row from `app.html:920,938,960`.
- **Data** — the column stays in prod Postgres (dropping columns at boot is
  riskier than leaving one unread). Stop reading and writing it; note it as
  dead in `migrations.py`.

## C. Reviews written inside Luna

### The problem

Today the pane's review block ends in a link:
"Write yours on the marketplace ↗"
([app.js:685](../marketplace-src/plugin_marketplace_ui/ui/app.js#L685)) which
opens `luna-marketplaces.onrender.com`, where the visitor is anonymous. The
hosted form itself requires a *certified* account
([reviews.py:170](../service/app/routers/reviews.py#L170)), i.e. one that has
linked a Luna install.

### What already exists

`service/app/routers/luna_link.py` defines a full enroll → link → signed-sync
handshake: an install gets a `secret`, and later requests are authenticated
with `HMAC_SHA256(secret, "{ts}.{body}")` via
`X-Luna-Tenant / X-Luna-Ts / X-Luna-Signature`.

### What does not exist

**Luna implements none of it.** There is no enroll call, no stored secret, no
signed request anywhere in the Luna repo. That is the actual gap.

### Design — the install is the identity, and it all lives in the plugin

**No Luna core changes.** `plugin-marketplace-ui` already has a server side
(`routes.py`) and `ctx.vault` gives it a per-plugin store whose own-namespace
reads and writes need no grant. There is no egress enforcement in Luna, so the
plugin can call the marketplace directly. That means reviews ship by publishing
a plugin version — no version bump, no image build, no fleet promote.

Accepted consequences: only installs that have the UI plugin can write reviews
(the degraded core pane has no review UI anyway), and the install identity is
owned by the plugin — so it is stored under a fixed vault key and the
marketplace re-binds the same `install_id` on re-enroll, making
uninstall/reinstall idempotent rather than identity-splitting.

1. **Plugin server side (`plugin_marketplace_ui/routes.py` + new `link.py`)**
   - Lazily `POST /api/luna/enroll` on first review action, using an
     `install_id` generated once and kept in the vault; store `{tenant, secret}`
     under the same key. Secret is never logged and never sent to the browser.
   - New routes under the plugin's own prefix:
     `GET  /api/p/plugin-marketplace-ui/reviews/{src}/{plugin}` (proxy read)
     `POST /api/p/plugin-marketplace-ui/reviews/{src}/{plugin}` (rating, title,
     body) → signs `HMAC_SHA256(secret, "{ts}.{body}")` and forwards.
     `DELETE …/reviews/{src}/{plugin}` for withdrawing your own.
   - Manifest gains the declarative `vault_access` permission and the
     marketplace host in `egress_hosts` (metadata only, but it is what the
     catalog shows users).
   - A `display_name` setting (defaults to the Luna instance name) sent with
     the review.
2. **Marketplace side** — new `POST /api/luna/reviews/{mp}/{plugin}`,
   authenticated by the existing `_verify_signed` helper:
   - `Review.user_id` becomes nullable; add
     `luna_install_id` (FK, nullable) + `author_display`;
   - uniqueness becomes one review per **install** per plugin (new partial
     unique index alongside the existing per-user one);
   - reuse the existing guards: body required under 3 stars, publisher-org
     installs cannot review their own plugin, 10 writes/day per install;
   - reviews from an install that has actually reported the plugin in its sync
     payload are flagged `verified_install: true`; the UI badges them.
   - No marketplace account and **no redirect** required at any point.
3. **Plugin UI** — replace the external link with an in-pane form: star picker,
   title, body, submit, plus edit/delete of your own review. Testids
   `mp-review-star-<n>`, `mp-review-title`, `mp-review-body`, `mp-review-submit`,
   `mp-review-mine`.

**Trust note to keep in the plan record:** identity drops from "certified
marketplace account" to "a real Luna install". That is the trade the request
asks for; the verified-install badge and the per-install rate limit are what
keep it meaningful. The existing website review path stays for signed-in users.

## D. Remove all git URLs from plugins

- **Source** — delete the `Source: https://github.com/…` lines from the readme
  block of all 8 `marketplace-src/*/luna-plugin.toml` files (connectors,
  interview, cloudflare, files, recall, voice, charts, render).
- **Service** — stop populating `Plugin.source_url`: drop it from the publish
  path ([plugins.py:201](../service/app/routers/plugins.py#L201)) and from
  `seed.py:231` (which fabricates `https://github.com/luna-plugins/<name>`).
  Remove it from the API schemas and from the admin edit form (`app.html:921,942,961`).
- **UIs** — remove the `['Source', … Repository]` row from both detail views
  (`plugin_detail.html:339`, plugin-ui `app.js:728`).
- **Prod data** — one idempotent backfill run at boot (a `_DATA_FIXUPS` step in
  `migrations.py`, guarded by a marker row so it runs once):
  `UPDATE plugins SET source_url=NULL` and strip any line matching
  `^\s*Source:\s*https?://\S*git` from `plugins.readme`. Log the affected count.
- Third-party/vendored URLs inside plugin *code* (e.g. the ElevenLabs client
  bundle in plugin-voice) are out of scope — they are attribution comments in
  vendored libraries, not plugin metadata.

## E. No fake zero-star state

`rating_count == 0` currently renders `☆☆☆☆☆` + `0.0 out of 5`, which reads as
a bad rating rather than no data.

- **Card / header rows** — when `rating_count` is 0, render no stars; render a
  quiet `Write the first review` link that jumps to the review form (detail
  view) or opens the plugin detail (list view). Applies to
  `plugin-marketplace-ui app.js:739` and `plugin_detail.html:348`, plus catalog
  cards.
- **Ratings & Reviews section** — when the count is 0, replace the big average
  + histogram block entirely with an empty state: "No reviews yet — write the
  first one", with the C form directly beneath it. No `0.0`, no empty bars, no
  `☆` glyphs.
- `starsHtml()` gains an explicit "no rating" branch in both copies so the two
  panes cannot drift.
- Testids: `mp-rating-empty`, `mp-review-first`.

## F. Update banner names what it will update

The banner says only "2 updates available / Update all (2)"
([app.js:492-495](../marketplace-src/plugin_marketplace_ui/ui/app.js#L492)) —
you cannot tell *what* is about to change, and the only action is all-or-nothing.

- The count text becomes a control: clicking it opens a popover listing each
  pending upgrade — plugin display name, `installed → available` versions, and
  a per-row **Update** link that upgrades just that plugin and removes the row.
- The row name opens that plugin's detail page; the popover closes on outside
  click and on Escape.
- "Update all" stays as-is next to it.
- Blocked upgrades (incompatible) are listed in the same popover, greyed, with
  the reason — today they are invisible because the banner only counts
  `compatible && available`.
- Testids: `mp-upgrade-list`, `mp-upgrade-row-<name>`, `mp-upgrade-row-update-<name>`.
- Core's degraded pane has no banner at all, so this is plugin-only; no core
  duplication to keep in sync.

---

## Sequencing

1. **A** first and alone — the fleet is mid-rollout and every tenant should be
   on one version before anything else ships to it.
2. **B + D + E + C** together: they all land in the marketplace service, the
   hosted templates and `plugin-marketplace-ui`. One Render deploy, one plugin
   republish (version 1.1.0). With C moved into the plugin, **no Luna core
   change and no second image build are needed for any of 009**.
3. Fleet install of `plugin-marketplace-ui` last, once 1.1.0 is live in the
   prod catalog, so every tenant lands on the version that has reviews.

## Verification

- Dojo: extend the marketplace suite with `mp-rating-empty` on a plugin with no
  reviews, and a full write-review round trip through the pane (enroll → post →
  the review appears → edit → delete).
- Grep gates in CI-ish form: no `license` in any manifest or marketplace
  template; no `github.com` in `marketplace-src/*/luna-plugin.toml`; no
  `source_url` read path in the service.
- Both panes (core degraded + plugin) re-run through the existing lifecycle
  regression (install / upgrade / disable / enable / archive / delete).
- Post-deploy: hosted detail page for a plugin with zero reviews shows the
  empty state and no License/Source rows; a review written from inside Luna
  appears on the hosted page attributed to the install.

## Risks

- **A2 is unexplained.** If the re-run with real logs shows the commit landing
  but `is_main` still false, suspect a second writer (the control-plane web
  process) and stop to investigate rather than retrying blindly.
- Removing `license` from the DB *read* path while the column stays is safe;
  actually dropping the column is deliberately not in scope.
- Making `Review.user_id` nullable is a schema change on a table that already
  holds prod rows — additive only (new nullable columns + a new index), no
  backfill of existing reviews.
