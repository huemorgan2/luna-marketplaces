# 04 — The update banner says *which* plugins are pending

**Goal:** "2 updates available / Update all (2)" doesn't say what is about to
change. The banner now expands into a row per plugin, each with its own Update
link, and blocked updates say why instead of silently sitting in the count.

## Steps (real browser, tenant Luna)
1. Get a tenant into a state with at least two outdated plugins (install two
   plugins at an older version, or point at a marketplace serving newer ones).
2. Open the Marketplace pane. Read the banner
   (`[data-testid="mp-upgrade-banner"]`).
3. Click the banner text (`[data-testid="mp-upgrade-toggle"]`).
4. Read the expanded list (`[data-testid="mp-upgrade-list"]`): one row per
   plugin (`mp-upgrade-row-<name>`), each showing the plugin's name, the
   installed → available versions, and an Update link.
5. Click the Update link on **one** row only.
6. Watch the banner and the remaining row.
7. If any plugin has an update it cannot take (incompatible), hover/read that
   row.
8. Collapse the list again, then use **Update all**.

## Expected
- The collapsed banner still shows the count and Update all — the list is
  additive, and starts collapsed.
- Every pending plugin is named, with both versions visible; the row count
  equals the banner count.
- Updating one row updates only that plugin; the count drops by one, that row
  leaves the list, the other row stays, and the card behind it shows the new
  installed version.
- A blocked row is visibly distinct and states the reason rather than offering a
  link that fails.
- Update all clears the remaining rows and the banner disappears.

## Pass/Fail
- PASS: names, versions and per-row updates all behave as above.
- FAIL: a bare count with no names, a row whose Update does nothing or updates
  the wrong plugin, or a count that disagrees with the rows.

## Evidence
Screenshots: collapsed banner, expanded list with both rows, mid-update state,
banner after one row updated, empty/absent banner after Update all.
