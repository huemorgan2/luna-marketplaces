"""Plugin category taxonomy.

Every plugin has exactly one category (browsable facet). Set in
`luna-plugin.toml` (`category = "connectivity"`), validated at publish time;
unknown values are rejected. Legacy manifest values map onto the taxonomy.
"""

from __future__ import annotations

CATEGORIES: dict[str, str] = {
    "connectivity": "Connectivity",
    "ability": "Ability",
    "knowledge": "Knowledge",
    "automation": "Automation",
    "media": "Media",
    "development": "Development",
    "games": "Games & Fun",
    "communication": "Communication",
    "security": "Security",
}

# Values seen in pre-taxonomy manifests → taxonomy slug.
LEGACY_MAP: dict[str, str] = {
    "connectors": "connectivity",
    "global": "ability",
    "system": "security",
    "game": "games",
    "fun": "games",
    "dev": "development",
    "tools": "ability",
}


def normalize(value: str | None) -> str | None:
    """Map a manifest category to a taxonomy slug, or None if unknown/absent."""
    if not value:
        return None
    slug = value.strip().lower().replace(" ", "-")
    if slug in CATEGORIES:
        return slug
    return LEGACY_MAP.get(slug)


def label(slug: str | None) -> str:
    return CATEGORIES.get(slug or "", "Other")
