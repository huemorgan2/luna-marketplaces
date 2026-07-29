# 03 — Full review round trip, inside the pane

**Goal:** the owner writes a review where they already are. The pane enrolls the
install once, signs each request with that install's secret, and the marketplace
answers with `is_mine` so the pane can show and edit the owner's own review.
Nothing here needs a marketplace account and nothing redirects off the pane.

Backing coded test: `service/tests/test_luna_reviews.py`. This scenario proves
the same lifecycle through the real UI against the live marketplace.

## Steps (real browser, tenant Luna on image ≥ 0.53.000)
1. Open the tenant's Marketplace pane and pick a plugin (any — reviewing does
   not require having it installed; the *verified install* badge does).
2. Scroll to reviews. Confirm the write line is present
   (`[data-testid="mp-review-open"]`) and reads that the review is signed by
   this Luna, no account needed. Click it.
3. In the form (`[data-testid="mp-review-form"]`): click **1 star**, leave the
   body empty, submit. Read the status line.
4. Now click **4 stars**, type a headline and a body, submit.
5. Watch the reviews list without reloading the pane.
6. Open the same plugin's hosted page in a normal browser tab (logged out).
7. Back in the pane, click **Edit your review**, change the rating to 5 and edit
   the body, submit.
8. Click **Delete**, confirm the dialog.
9. Reload the pane and re-open the plugin.

## Expected
- Step 3: refused with a written-explanation message; nothing is posted.
- Step 4: the review posts and the form collapses.
- Step 5: the new review appears at the top marked as yours, the summary count
  and average update, and the plugin's card/detail rating updates too — all
  without a page reload.
- Step 6: the same review is visible to a logged-out visitor, attributed to the
  Luna's name (or the name typed), with a verified-install badge only if the
  plugin is actually installed on that Luna.
- Step 7: the review is **replaced**, not duplicated — count stays 1, the row is
  marked edited.
- Step 8: the review disappears, the count returns to 0, and the plugin falls
  back to the "Write the first review" empty state from scenario 02.
- Step 9: the state after reload matches what was on screen — no ghost review.
- At no point does the pane navigate to the hosted site or ask for a login.

## Pass/Fail
- PASS: every step above, including the 1-star refusal and single-review-per-
  install replacement.
- FAIL: a redirect or login prompt, a duplicate review after editing, a review
  that survives delete, or counts that only update after a reload.

## Evidence
Screenshots: write line, form with stars picked, 1-star refusal message, posted
review marked as yours, the hosted page showing it logged out, edited row,
empty state after delete.
