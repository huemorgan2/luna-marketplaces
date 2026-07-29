"""Schema definitions and validation for Luna marketplace documents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MarketplaceIdentity(BaseModel):
    """The /.well-known/luna-marketplace.json document."""

    id: str = Field(description="UUIDv7 marketplace identifier")
    name: str = Field(description="Human-readable marketplace name")
    protocol_version: str = Field(default="0.1.0")
    description: str = ""
    signing_keys: list[str] = Field(
        description="Ed25519 public keys (hex), first is current"
    )
    endpoints: dict[str, str] = Field(
        default_factory=lambda: {
            "index": "/index.json",
            "plugins": "/plugins/{name}/versions.json",
            "artifact": "/plugins/{name}/{version}/artifact.zip",
        }
    )
    auth: AuthConfig | None = None
    previous_keys: list[str] = Field(
        default_factory=list,
        description="Rotated keys (for signature chain verification)",
    )


class AuthConfig(BaseModel):
    """Auth hint for private marketplaces."""

    type: Literal["bearer", "mtls"] = "bearer"
    token_url: str | None = None


class PluginCompat(BaseModel):
    """Compatibility requirements for a plugin."""

    sdk: str = Field(description="SDK version range, e.g. '^1.2'")
    requires: dict[str, str] = Field(
        default_factory=dict,
        description="Capability requirements, e.g. {'tools': '>=2'}",
    )


class PluginProvenance(BaseModel):
    """Origin and trust metadata for a plugin."""

    publisher_key: str | None = Field(
        default=None, description="Ed25519 public key of the publisher"
    )


class PluginPermissions(BaseModel):
    """What the plugin declares it needs/does."""

    tools: list[ToolPermission] = Field(default_factory=list)
    egress_hosts: list[str] = Field(default_factory=list)
    provider: str | None = None
    vault_access: bool = False
    ui_iframe: bool = False
    settings_tab: bool = False


class ToolPermission(BaseModel):
    """Permission metadata for a declared tool."""

    name: str
    policy: Literal["auto_approve", "prompt_first_time_only", "prompt_always", "block"] = "auto_approve"
    description: str = ""


class PublishedManifest(BaseModel):
    """The published form of a plugin manifest in a marketplace."""

    name: str
    namespace: str
    version: str
    description: str = ""
    requires_entitlement: str | None = None

    compat: PluginCompat
    provenance: PluginProvenance = Field(default_factory=PluginProvenance)
    permissions: PluginPermissions = Field(default_factory=PluginPermissions)

    readme: str | None = Field(default=None, description="Markdown README content")
    tags: list[str] = Field(default_factory=list)
    icon: str | None = Field(default=None, description="URL or path to icon")

    @property
    def full_name(self) -> str:
        return f"{self.namespace}/{self.name}"


class VersionEntry(BaseModel):
    """A single version in the versions.json per-plugin file."""

    version: str
    artifact_hash: str = Field(description="SHA-256 of the artifact zip")
    manifest_hash: str = Field(description="SHA-256 of the manifest JSON")
    published_at: int = Field(description="Unix timestamp")
    yanked: bool = False
    compat: PluginCompat


class PluginVersions(BaseModel):
    """Per-plugin versions file: /plugins/{name}/versions.json payload."""

    name: str
    namespace: str
    versions: list[VersionEntry] = Field(default_factory=list)
    latest: str | None = None


class IndexEntry(BaseModel):
    """Summary entry in the marketplace index."""

    name: str
    namespace: str
    latest_version: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)


class MarketplaceIndex(BaseModel):
    """The top-level index.json payload."""

    marketplace_id: str
    generated_at: int
    plugin_count: int = 0
    plugins: list[IndexEntry] = Field(default_factory=list)


class Timestamp(BaseModel):
    """Freshness timestamp document."""

    signed_at: int
    expires_at: int
    version: int = Field(description="Monotonically increasing counter for rollback protection")


class Snapshot(BaseModel):
    """Snapshot: maps filenames to their content hashes."""

    signed_at: int
    version: int
    files: dict[str, str] = Field(
        description="Map of relative path -> SHA-256 hash of the signed payload"
    )
