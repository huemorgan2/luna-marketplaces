# 02 — `mp-rating-empty`: no reviews reads as "no reviews", not as zero stars

**Goal:** five grey stars next to a plugin nobody has reviewed reads as a
one-star rating. A plugin with no reviews must invite the first one instead.

## Steps (real browser)
1. Find a plugin in `official` with `rating_count == 0` — check
   `https://luna-marketplaces.onrender.com/api/catalog/official` and pick a name
   whose `rating_count` is 0.
2. Open its hosted page `/browse/official/plugin/<name>`.
3. Read the line under the title (where a rating would go) and the reviews
   section further down (`[data-testid="mp-rating-empty"]` and
   `[data-testid="mp-review-first"]`).
4. Click "Write the first review" and observe where the page moves to.
5. Open the same plugin in the pane (tenant Luna → Marketplace → the plugin) and
   read the same two places.
6. For contrast, open a plugin that *does* have reviews on both surfaces.

## Expected
- Zero-review plugin, both surfaces: the title line shows a **"Write the first
  review"** link, not stars and not "0.0". The reviews section shows
  "No reviews yet — be the first to say what this plugin is like." and no
  histogram of empty bars presented as a rating.
- Clicking the link scrolls to / opens the write affordance rather than doing
  nothing.
- Reviewed plugin: stars, the average to one decimal, and the count all appear
  as before — the empty state is conditional, not a replacement.

## Pass/Fail
- PASS: no empty-star row or `0.0` average appears for an unreviewed plugin on
  either surface, and the invitation is clickable.
- FAIL: grey stars, a `0.0`/`(0)` rating, or a dead link.

## Evidence
Screenshots: hosted page title line + reviews section for a zero-review plugin,
the same two in the pane, and a reviewed plugin for contrast.
