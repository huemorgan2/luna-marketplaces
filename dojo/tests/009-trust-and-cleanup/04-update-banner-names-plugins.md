# 04 — The update banner says *which* plugins are pending

**Goal:** "2 updates available / Update all (2)" doesn't say what is about to
change. The banner shows a row per plugin, each with its own Update link, and
blocked updates say why instead of silently sitting in the count. The list is
open by default — behind a caret, nobody found it.

## Steps (real browser, tenant Luna)
1. Get a tenant into a state with at least two outdated plugins (install two
   plugins at an older version, or point at a marketplace serving newer ones).
2. Open the Marketplace pane. Read the banner
   (`[data-testid="mp-upgrade-banner"]`) **and the list under it**
   (`[data-testid="mp-upgrade-list"]`) without clicking anything.
3. Read the rows: one per plugin (`mp-upgrade-row-<name>`), each showing the
   plugin's name, the installed → available versions, and an Update link.
4. Click the Update link on **one** row only.
5. Watch the banner and the remaining row.
6. If any plugin has an update it cannot take (incompatible), hover/read that
   row.
7. Click the banner text (`[data-testid="mp-upgrade-toggle"]`) to collapse the
   list, click again to re-open, then use **Update all**.

## Expected
- The list is visible on load — no click needed — and the toggle still
  collapses/expands it (caret ▴ open, ▾ collapsed).
- Every pending plugin is named, with both versions visible; the row count
  equals the banner count.
- Updating one row updates only that plugin; the count drops by one, that row
  leaves the list, the other row stays, and the card behind it shows the new
  installed version.
- A blocked row is visibly distinct and states the reason rather than offering a
  link that fails.
- Update all clears the remaining rows and the banner disappears.
- **The banner does not come back.** Re-open the pane: an applied update stays
  applied and the plugin reports its new version (the stale-`version=`-literal
  bug — an update that reported success forever; see
  `service/tests/test_manifest_version_sync.py`).

## Pass/Fail
- PASS: names, versions and per-row updates all behave as above, and the banner
  stays gone after a reload.
- FAIL: a bare count with no names, a list that needs a click to appear, a row
  whose Update does nothing or updates the wrong plugin, a count that disagrees
  with the rows, or an update that reappears after it was applied.

## Evidence
Screenshots: banner with its list on first paint, mid-update state, banner after
one row updated, empty/absent banner after Update all, pane after a reload.
