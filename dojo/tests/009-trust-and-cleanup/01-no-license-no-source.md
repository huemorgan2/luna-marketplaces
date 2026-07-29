# 01 — No license, no source URL, on any surface

**Goal:** a plugin is described by what it does, not by a licence chip or a
GitHub link. Both were removed in 009 (B and D) from manifests, the API, the
hosted site and both panes; prod rows were nulled by a data fixup.

## Steps (real browser)
1. Open `https://luna-marketplaces.onrender.com/browse/official`. Scan the cards.
2. Open three plugin pages, at least one published before 009 (e.g.
   `hello-world`, `plugin-recall`, `plugin-marketplace-ui`).
3. On each page read the info table (Publisher / Version / Category / Downloads /
   Compatibility / First published / Updated), the Requirements grid, the
   versions table and the rendered README.
4. Open the pane in a tenant Luna → Marketplace, open the same plugins' detail
   views, and read every row.
5. Use the catalog's category filter — read the full list of categories offered.

## Expected
- No "License" row, chip or column, and no licence text (MIT, Apache-2.0, …) on
  any card, detail page, versions table or requirements grid.
- No "Source", "Repository" or GitHub link anywhere, and no `Source: <url>` line
  left at the top of a README.
- "License" is not offered as a category in the catalog filter.
- Pages still render fully — nothing is left as an empty row or dangling label
  where the removed fields used to be.

## Pass/Fail
- PASS: neither word appears on either surface and the layouts are intact.
- FAIL: any licence or source/repo affordance survives, or a layout has a hole
  where one was removed.

## Evidence
Screenshots: catalog grid, category filter open, two hosted plugin pages
(info table in frame), pane detail view of the same plugin.
