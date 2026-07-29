# 005 — Marketplace experience: test report (2026-07-29)

Local run against a fresh sqlite DB (`/tmp/smoke.db`) on :8077, seeded from
marketplace-src at boot. Browser: headless Chromium (Playwright).

| # | Scenario | Result |
|---|----------|--------|
| 1 | Service pytest suite | PASS — 27 passed |
| 2 | enroll (new install) | PASS — tenant + secret + code (e.g. `QRE3TXQ4`) |
| 3 | vault storage of secret/tenant/install_id | PASS — `plugin_marketplace.install_id`, `.mp_secret.<host>`, `.mp_tenant.<host>` |
| 4 | signed sync | PASS — `{status: ok, installed_count: 2, linked: false}` |
| 5 | idempotent re-enroll | PASS — `existing=true`, no secret re-issue |
| 6 | sync while unenrolled | PASS — HandshakeError before any network call |
| 7 | bad signature | PASS — 401 "bad signature" |
| 8 | link flow (sign-up → /link → certify) | PASS — "linked ok" |
| 9 | certified review | PASS — review posted, histogram + 5.0 average render |
| 10 | Installed pills + gear deep-link | PASS — 3 pills, 1 gear (`/settings/plugins/…`) |
| 11 | JS console errors | PASS — none on Discover / detail / link |
| 12 | 13 manifests: category + bump + media | PASS — all package; icons/covers seeded, media excluded from zips |
| 13 | Discover rendering | PASS — one fix applied: discover() no longer defaults `hero_title` to the raw plugin name (fallback hero showed `plugin-cloudflare`; now "Cloudflare") |
| 14 | Luna `pnpm build` | PASS — tsc + vite clean |
| 15 | Luna marketplace pytest suites | PASS — 27 passed |
| 16 | Settings Connect card | Built; verified by code review + build (no live Luna booted in this run) |
| 17 | `/settings/plugins/{name}` deep-link | Built; parseRoute unit path verified by build (same caveat) |

Screenshots: `discover.png`, `detail.png`, `link.png`, `e2e-*.png` in the
session scratchpad.

Notes
- Demo reviews exist only in `service/seed.py` (demo DB); the official
  marketplace gets reviews exclusively from real certified users.
- Handshake secrets never appear in logs (verified in `handshake.py` — only
  origin/linked/existing/count fields are logged).
