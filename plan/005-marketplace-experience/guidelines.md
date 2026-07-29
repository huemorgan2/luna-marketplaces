# Marketplace Experience Guidelines

What every page of the Luna marketplace contains, and the rules for the content in it.
Modeled on the Apple App Store product page, adapted for an agent-plugin marketplace: an agent has no camera roll or GPS — its "privacy label" is tools, data, and network reach.

---

## 1. Category taxonomy

Every plugin has exactly **one category** (browsable facet) and up to **8 free-form tags** (searchable keywords).

| Category | What belongs there |
|---|---|
| **Connectivity** | Integrations with external services: email, calendars, Slack, home automation, APIs |
| **Ability** | New skills for the agent: charts, file conversion, OCR, code execution, math |
| **Knowledge** | Memory, notes, RAG, knowledge bases, bookmarks |
| **Automation** | Schedulers, watchers, triggers, background jobs |
| **Media** | Image/audio/video generation and processing |
| **Development** | Dev tools: git, CI, deploy, log readers, linters |
| **Games & Fun** | Games, personality packs, toys |
| **Communication** | Messaging, voice, notifications to humans |
| **Security** | Vaults, secrets, auditing, permission tooling |

Rules: category is set in `luna-plugin.toml` (`category = "connectivity"`), validated at publish time against this list; unknown → publish rejected. Tags are lowercase kebab-case, deduplicated, no category names as tags.

## 2. Discover (browse) page

Editorial scroll, App-Store layout. Section order:

1. Hero pair — 2 full-bleed cover cards (curated).
2. **Essentials** — 3 cards, curated must-haves.
3. Large feature cards — 2 cover-on-top cards (curated).
4. **Top picks** — 3 cards, highest-rated.
5. One section per category: title left, "More {category} »" right (only when the category has ≥4 plugins), 3 plugin cards. Category page lists all as rows.

Rules:
- Sticky header: marketplace name, search, sign-in.
- Plugin card: large icon (72px, uploaded, emoji fallback), name, version, one-line description, Install/Installed state. Star rating is NOT shown on cards — only on the detail page.
- Cards carry a very faint per-category gradient tint over the glass surface.
- Covers are generated illustrations in the Luna icon language (matte stone 3D, faint violet glow, dark quiet text zone) — never enlarged icons.
- **Installed state**: the Luna client knows its installed plugins; cards, rows and the detail page show an "Installed" badge instead of Install. If the plugin registers a settings tab in Luna, show a settings link (gear) that deep-links to Settings → that plugin.
- Pagination on category pages (24/page). No infinite unpaginated fetch.

## 3. Plugin detail page

Section order (top → bottom), matching `mock.html`:

1. **Header** — icon, name, subtitle (one-liner), publisher/namespace, category, star average + review count, download count, Install button (copies Luna config / deep-links into agent), version + updated date.
2. **Screenshot gallery** — horizontal carousel, 1–10 items. Kinds: UI screenshots (for iframe plugins), **chat transcripts** (the agent using the plugin — the agent-world equivalent of an app preview video), optional short GIF/video. Captions required. If no media: render the readme hero only, never placeholder boxes.
3. **Agent Access label** (App-Privacy-style, always present, generated from the manifest — publisher cannot edit it directly):
   - Tools: count, each with name, approval policy (auto-approve / prompt-first-time / prompt-always) and risk level badge (low/medium/high).
   - Data: DB tables it owns, vault access yes/no.
   - Network: egress domains (or "No network access").
   - Environment: required env vars, plugin dependencies (`depends_on`).
4. **Description** — readme markdown.
5. **What's New** — latest version's changelog + link to full version history (with yanked versions marked).
6. **Ratings & Reviews** — summary block (big average, star histogram 5→1, total count), 3 most-helpful reviews inline, "See all N reviews" → full page.
7. **Information** — version, SDK compatibility, license, size (artifact bytes), publisher, source link, first published / last updated.
8. **More in {category}** — up to 6 related plugins.

## 4. Ratings & reviews rules

- Stars 1–5, integer, required. Title (≤80 chars) and body (≤2000 chars) optional but a body is required for 1–2 star ratings (say what broke).
- One review per user per plugin; editable any time (marked "edited"); rating counts once.
- Reviewer must be a signed-in user; members of the publishing org cannot review their own plugin.
- Review shows: stars, title, body, author username, date, **plugin version reviewed**, "helpful" count.
- Sort options on the full reviews page: Most Helpful (default), Most Recent, Most Critical.
- Publisher may post **one response** per review, shown nested under it.
- "Helpful" is one vote per user per review; no downvotes.
- Moderation v1: marketplace owner can delete any review; rate limit 10 reviews/user/day. Future: report flow, LLM review summary paragraph (like iOS 18.4).
- Never seed fake reviews into the official marketplace; demo reviews live only in `service/seed.py` demo data.

## 5. Media rules

- Up to 10 items per plugin; PNG/WebP/GIF/MP4; ≤8 MB each; recommended 1280×800 (UI) or 800×1000 (chat transcript).
- Stored content-addressed in the existing artifact store; served from `/media/{sha256}` with immutable cache headers.
- Sources: `[media]` entries in `luna-plugin.toml` referencing files inside the plugin zip, or uploaded via the dashboard.
- First item = card hero on the catalog page. Captions mandatory.
- Icon: square PNG/SVG ≥256px via manifest `icon`; hash-emoji fallback stays for plugins without one.

## 6. What an *agent* marketplace needs that an app store doesn't

These are first-class, not buried:

- **Trust & integrity**: sha256 of artifact shown on the page; signature status; "yanked" versions clearly marked.
- **Approval-policy preview**: users decide install largely on "how often will this interrupt me / what can it silently do" — the Agent Access label answers that above the fold.
- **Dependency graph**: `depends_on` plugins linked, with an "installs X too" note on the Install button.
- **SDK compatibility**: shown like iOS version requirements; incompatible plugins render a warning, not a hidden entry.
- **Chat-transcript media**: the primary "screenshot" of a headless plugin is a transcript of the agent using it well.
- **Resource footprint**: tables created, background jobs, env vars needed — surfaced in Information.

## 7. Visual language

Keep the existing identity: dark theme (`--bg:#0f0f13`, surface `#17171d`, accent `#7c6cf0`), Inter, 12px radius, no build step — Jinja + vanilla JS. Stars use `#f5a623`.

Responsive rule: the layout never collapses to a flat list and fonts never shrink below their base size. Below 980px the card grids drop from 3 to 2 columns (rows to 1) with everything else unchanged; the target is a window at ~45% of a laptop screen (~650px). Only below 640px does the whole page scale down (`zoom = min(1, innerWidth/640)`; viewport meta `width=720` for phones).
