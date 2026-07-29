# Dojo — Plan 009: Trust and cleanup

Browser scenarios for `plan/009-trust-and-cleanup/PLAN.md`: plugins carry no
license and no source URL anywhere, a plugin with no reviews says so instead of
showing five grey stars, and the owner of a Luna can write a review **from
inside the pane** — signed by the install, no account, no redirect to a site
that doesn't know them.

These are LLM-driven browser tests (see `luna/dojo/vision/vision.md`): read the
DOM, take screenshots, judge behavior. Coded tests live in `service/tests/`
(`test_luna_reviews.py` covers the signed write/read/edit/delete lifecycle).

## Surfaces under test
- **Hosted marketplace:** `https://luna-marketplaces.onrender.com/browse/official`
  and `/browse/official/plugin/<name>`
- **Pane (plugin-marketplace-ui ≥ 1.1.0):** a tenant Luna → Marketplace pane.
  The pane is served by the plugin, not by Luna core; a tenant on a Luna image
  older than 0.53.000 will not have it.

## Scenarios
| # | File | Proves |
|---|------|--------|
| 01 | `01-no-license-no-source.md` | Neither surface shows a License or Source/GitHub row, anywhere |
| 02 | `02-rating-empty-states.md` | Zero-review plugins invite a first review instead of rendering empty stars |
| 03 | `03-review-round-trip-in-pane.md` | Write → appears → edit → delete, entirely inside the pane |
| 04 | `04-update-banner-names-plugins.md` | The update banner lists which plugins are pending, each with its own Update |

## Results
Write run results to `dojo/results/NNNN-009-trust-and-cleanup/` with `summary.md`
and a `screenshots/` folder, per the dojo convention.
