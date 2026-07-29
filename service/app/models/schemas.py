"""Pydantic schemas for API request/response."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    email: str
    username: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    created_at: int


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PublishTokenInfo(BaseModel):
    """Metadata about the caller's active publish token — never the secret."""

    exists: bool
    token_prefix: str | None = None
    created_at: int | None = None
    last_used_at: int | None = None


class PublishTokenCreated(BaseModel):
    """The full secret, returned exactly once at creation."""

    token: str
    token_prefix: str
    created_at: int


class OrgCreate(BaseModel):
    name: str
    slug: str


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    created_at: int


class MarketplaceCreate(BaseModel):
    name: str
    slug: str
    description: str = ""
    visibility: str = "public"


class MarketplaceResponse(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    visibility: str
    signing_key_public: str | None = None
    access_token: str | None = None
    created_at: int
    plugin_count: int = 0


class MyMarketplaceResponse(MarketplaceResponse):
    """A marketplace the current user can access, with their relationship to it."""

    org_name: str = ""
    org_slug: str = ""
    # "created" = user owns the org; "shared" = member/global-editor of it.
    group: str = "shared"
    # The user's effective role: owner | publisher | reviewer | viewer | admin.
    access: str = "viewer"
    can_edit: bool = False


class MediaItem(BaseModel):
    """One plugin media entry, servable at /media/{sha256}."""

    kind: str  # icon | cover | screenshot
    sha256: str
    content_type: str = "image/png"
    caption: str = ""
    url: str = ""  # "/media/{sha256}"


class PluginResponse(BaseModel):
    id: str
    name: str
    namespace: str
    description: str
    readme: str
    tags: list[str]
    license: str
    icon_url: str | None
    source_url: str | None
    latest_version: str | None
    download_count: int
    created_at: int
    updated_at: int
    requires_tools: bool
    requires_ui_iframe: bool
    requires_settings_tab: bool
    requires_vault_access: bool
    requires_egress: list[str]
    tool_count: int
    tool_policies: list[dict]
    marketplace_slug: str = ""
    marketplace_name: str = ""
    category: str | None = None
    rating_average: float = 0.0
    rating_count: int = 0
    media: list[MediaItem] = Field(default_factory=list)


class PluginVersionResponse(BaseModel):
    id: str
    version: str
    artifact_hash: str
    sdk_compat: str
    capabilities_required: dict
    published_at: int
    yanked: bool


class PluginUpdate(BaseModel):
    """Editable plugin metadata (all optional — only provided fields change)."""

    description: str | None = None
    readme: str | None = None
    tags: list[str] | None = None
    license: str | None = None
    source_url: str | None = None
    icon_url: str | None = None
    category: str | None = None


class YankRequest(BaseModel):
    yanked: bool = True


class BundleItem(BaseModel):
    """A pinned member plugin inside a bundle version."""

    plugin_name: str
    version: str


class BundleCreate(BaseModel):
    """Create a bundle together with its first version."""

    name: str  # slug-like identifier, unique per marketplace
    title: str
    version: str = "1.0.0"
    description: str = ""
    readme: str = ""
    tags: list[str] = Field(default_factory=list)
    icon_url: str | None = None
    items: list[BundleItem]


class BundleUpdate(BaseModel):
    """Editable bundle marketing metadata (only provided fields change)."""

    title: str | None = None
    description: str | None = None
    readme: str | None = None
    tags: list[str] | None = None
    icon_url: str | None = None


class BundleVersionCreate(BaseModel):
    """Publish a new bundle version with a (possibly changed) pin set."""

    version: str
    items: list[BundleItem]


class BundleItemResolved(BundleItem):
    """A pin enriched with the member plugin's catalog state."""

    description: str = ""
    icon_url: str | None = None
    latest_available: str | None = None  # the plugin's own latest version
    exists: bool = True


class BundleVersionResponse(BaseModel):
    id: str
    version: str
    items: list[BundleItem]
    published_at: int
    yanked: bool


class BundleResponse(BaseModel):
    id: str
    name: str
    title: str
    description: str
    readme: str
    tags: list[str]
    icon_url: str | None
    latest_version: str | None
    download_count: int
    created_at: int
    updated_at: int
    items: list[BundleItemResolved] = Field(default_factory=list)
    marketplace_slug: str = ""
    marketplace_name: str = ""


class PluginPublish(BaseModel):
    """Metadata submitted alongside the artifact upload."""
    manifest: dict


class CatalogFilter(BaseModel):
    tags: list[str] = Field(default_factory=list)
    license: str | None = None
    requires_ui: bool | None = None
    requires_vault: bool | None = None
    search: str | None = None


# --- Reviews ---------------------------------------------------------------

class ReviewCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    title: str = Field(default="", max_length=80)
    body: str = Field(default="", max_length=2000)


class ReviewResponse(BaseModel):
    id: str
    plugin_id: str
    rating: int
    title: str
    body: str
    author: str  # username
    plugin_version: str
    helpful_count: int
    created_at: int
    updated_at: int
    edited: bool
    response_body: str | None = None
    response_at: int | None = None
    is_mine: bool = False
    voted_helpful: bool = False


class ReviewSummary(BaseModel):
    average: float
    count: int
    histogram: dict[int, int]  # star -> count, keys 1..5


class PublisherResponseCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


# --- Luna handshake ---------------------------------------------------------

class LunaEnrollRequest(BaseModel):
    install_id: str = Field(min_length=8, max_length=128)
    luna_name: str = ""
    luna_version: str = ""
    base_url: str | None = None


class LunaEnrollResponse(BaseModel):
    tenant: str  # == install record id
    secret: str | None = None  # only on first enroll; never re-issued
    link_code: str | None = None  # present until linked
    link_url: str = ""
    linked: bool = False
    existing: bool = False


class LinkLunaRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
