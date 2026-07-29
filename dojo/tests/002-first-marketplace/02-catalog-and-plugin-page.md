# 02 — Browsable catalog + developer plugin page

**Goal:** humans can browse the marketplace and a plugin's page shows the detail
a developer expects (downloads, versions, permissions, add-to-Luna URL).

## Steps (real browser)
1. Open `https://luna-marketplaces.onrender.com/browse/official`.
2. Observe the catalog grid; find the `hello-world` card. Note its download count badge.
3. Use the search box to filter for "hello"; confirm the card stays.
4. Click the `hello-world` card → lands on `/browse/official/plugin/hello-world`.
5. On the plugin page, read: stats row (Downloads / Versions / Tools — 009
   removed License and Source; see `dojo/tests/009-trust-and-cleanup/01`),
   the **Add to Luna** box with the marketplace URL + Copy button, the
   Requirements grid, Declared Tools (`hello_world`, auto-approve), the Versions
   table (0.1.0, Active), and the rendered README.

## Expected
- Catalog renders cards (not an empty state); filters and search work.
- Plugin page shows a non-broken layout with the stats row and a copyable
  marketplace URL `https://luna-marketplaces.onrender.com/mp/official/`.
- `hello_world` tool listed with an `auto approve` policy chip.
- README renders as formatted HTML (headings, code).

## Pass/Fail
- PASS: catalog + plugin page render correctly with the above elements.
- FAIL: empty/broken catalog, plugin page 404, missing stats or tools, or the
  add-to-Luna URL wrong/absent.

## Evidence
Screenshots: catalog grid, filtered grid, plugin page top (stats + add-to-Luna),
plugin page tools + versions + README.
