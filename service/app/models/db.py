"""Luna Marketplaces Service — database models."""

from __future__ import annotations

import time
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON, Float, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def gen_uuid() -> str:
    return str(uuid.uuid4())


def now_ts() -> int:
    return int(time.time())


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=gen_uuid)
    email = Column(String, unique=True, nullable=False, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    created_at = Column(Integer, default=now_ts)
    is_active = Column(Boolean, default=True)
    # Set when the user links a real Luna install (handshake). Certified users
    # may write reviews. Cleared when the last install is unlinked.
    certified_at = Column(Integer, nullable=True)

    memberships = relationship("OrgMember", back_populates="user")


class Org(Base):
    __tablename__ = "orgs"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    plan = Column(String, default="free")
    created_at = Column(Integer, default=now_ts)

    members = relationship("OrgMember", back_populates="org")
    marketplaces = relationship("Marketplace", back_populates="org")


class OrgMember(Base):
    __tablename__ = "org_members"

    id = Column(String, primary_key=True, default=gen_uuid)
    org_id = Column(String, ForeignKey("orgs.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    role = Column(String, default="viewer")  # owner, publisher, reviewer, viewer
    joined_at = Column(Integer, default=now_ts)

    org = relationship("Org", back_populates="members")
    user = relationship("User", back_populates="memberships")


class Marketplace(Base):
    __tablename__ = "marketplaces"

    id = Column(String, primary_key=True, default=gen_uuid)
    org_id = Column(String, ForeignKey("orgs.id"), nullable=False)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text, default="")
    visibility = Column(String, default="public")  # public, unlisted, private
    signing_key_public = Column(String, nullable=True)
    signing_key_private_encrypted = Column(String, nullable=True)
    access_token = Column(String, nullable=True)
    # Editorial curation for the Discover page:
    # {"heroes":[{plugin,kicker,title,sub}], "essentials":[names], "features":[{...}]}
    curation = Column(JSON, nullable=True)
    created_at = Column(Integer, default=now_ts)

    org = relationship("Org", back_populates="marketplaces")
    plugins = relationship("Plugin", back_populates="marketplace")


class PublishToken(Base):
    """Long-lived publish credential, scoped to one (user, marketplace).

    The secret (`lmp_` + urlsafe random) is shown once at creation; only its
    sha256 is stored. Presented as `Authorization: Bearer lmp_...` on the
    publish/manage APIs.
    """
    __tablename__ = "publish_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    marketplace_id = Column(String, ForeignKey("marketplaces.id"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, index=True)
    token_prefix = Column(String, nullable=False)
    created_at = Column(Integer, default=now_ts)
    last_used_at = Column(Integer, nullable=True)
    revoked = Column(Boolean, default=False)

    user = relationship("User")
    marketplace = relationship("Marketplace")


class Plugin(Base):
    __tablename__ = "plugins"

    id = Column(String, primary_key=True, default=gen_uuid)
    marketplace_id = Column(String, ForeignKey("marketplaces.id"), nullable=False)
    name = Column(String, nullable=False, index=True)
    namespace = Column(String, nullable=False)
    description = Column(Text, default="")
    readme = Column(Text, default="")
    tags = Column(JSON, default=list)
    license = Column(String, default="MIT")
    icon_url = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    latest_version = Column(String, nullable=True)
    download_count = Column(Integer, default=0)
    category = Column(String, nullable=True, index=True)  # taxonomy slug, see app/taxonomy.py
    rating_average = Column(Float, default=0.0)  # denormalized from reviews
    rating_count = Column(Integer, default=0)
    created_at = Column(Integer, default=now_ts)
    updated_at = Column(Integer, default=now_ts)

    # Manifest requirements summary
    requires_tools = Column(Boolean, default=False)
    requires_ui_iframe = Column(Boolean, default=False)
    requires_settings_tab = Column(Boolean, default=False)
    requires_vault_access = Column(Boolean, default=False)
    requires_egress = Column(JSON, default=list)
    tool_count = Column(Integer, default=0)
    tool_policies = Column(JSON, default=list)

    marketplace = relationship("Marketplace", back_populates="plugins")
    versions = relationship("PluginVersion", back_populates="plugin")


class PluginVersion(Base):
    __tablename__ = "plugin_versions"

    id = Column(String, primary_key=True, default=gen_uuid)
    plugin_id = Column(String, ForeignKey("plugins.id"), nullable=False)
    version = Column(String, nullable=False)
    artifact_hash = Column(String, nullable=False)
    manifest_hash = Column(String, nullable=False)
    manifest_data = Column(JSON, nullable=False)
    sdk_compat = Column(String, default="^1.0")
    capabilities_required = Column(JSON, default=dict)
    published_at = Column(Integer, default=now_ts)
    yanked = Column(Boolean, default=False)

    plugin = relationship("Plugin", back_populates="versions")


class Bundle(Base):
    """A curated, marketed group of existing plugins in one marketplace.

    A bundle has its own marketing surface (title, image, readme, tags) and its
    own version line. Each BundleVersion pins exact member plugin versions —
    a member plugin releasing a new version never changes a bundle; an editor
    must publish a new bundle version with updated pins.
    """
    __tablename__ = "bundles"

    id = Column(String, primary_key=True, default=gen_uuid)
    marketplace_id = Column(String, ForeignKey("marketplaces.id"), nullable=False)
    name = Column(String, nullable=False, index=True)  # slug-like, unique per marketplace
    title = Column(String, nullable=False, default="")  # display name
    description = Column(Text, default="")
    readme = Column(Text, default="")
    tags = Column(JSON, default=list)
    icon_url = Column(String, nullable=True)
    latest_version = Column(String, nullable=True)
    download_count = Column(Integer, default=0)
    created_at = Column(Integer, default=now_ts)
    updated_at = Column(Integer, default=now_ts)

    marketplace = relationship("Marketplace")
    versions = relationship("BundleVersion", back_populates="bundle")


class BundleVersion(Base):
    """An immutable bundle release: a version string + explicit plugin pins."""
    __tablename__ = "bundle_versions"

    id = Column(String, primary_key=True, default=gen_uuid)
    bundle_id = Column(String, ForeignKey("bundles.id"), nullable=False)
    version = Column(String, nullable=False)
    # [{"plugin_name": "plugin-wiki", "version": "0.3.2"}, ...]
    items = Column(JSON, nullable=False, default=list)
    published_at = Column(Integer, default=now_ts)
    yanked = Column(Boolean, default=False)

    bundle = relationship("Bundle", back_populates="versions")


class Artifact(Base):
    """Metadata for a stored artifact. Bytes live on the mounted disk,
    content-addressed by sha256 (see app/storage.py)."""
    __tablename__ = "artifacts"

    sha256 = Column(String, primary_key=True)
    size = Column(Integer, nullable=False, default=0)
    created_at = Column(Integer, default=now_ts)


class Review(Base):
    """One review per user per plugin (unique pair). Editable; rating counts once.

    Only certified users (users.certified_at set — they linked a real Luna
    install) may create reviews. The publisher's own org cannot review its plugin.
    """
    __tablename__ = "reviews"

    id = Column(String, primary_key=True, default=gen_uuid)
    plugin_id = Column(String, ForeignKey("plugins.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)  # 1..5
    title = Column(String, default="")
    body = Column(Text, default="")
    plugin_version = Column(String, default="")  # version string at review time
    helpful_count = Column(Integer, default=0)
    created_at = Column(Integer, default=now_ts)
    updated_at = Column(Integer, default=now_ts)
    edited = Column(Boolean, default=False)
    response_body = Column(Text, nullable=True)  # single publisher response
    response_at = Column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("plugin_id", "user_id", name="uq_review_plugin_user"),)


class ReviewVote(Base):
    """One 'helpful' vote per user per review. No downvotes."""
    __tablename__ = "review_votes"

    id = Column(String, primary_key=True, default=gen_uuid)
    review_id = Column(String, ForeignKey("reviews.id"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    created_at = Column(Integer, default=now_ts)

    __table_args__ = (UniqueConstraint("review_id", "user_id", name="uq_vote_review_user"),)


class PluginMedia(Base):
    """Plugin media (icon / cover / screenshot). Bytes are content-addressed in
    the artifact store (extension-aware); served at GET /media/{sha256}."""
    __tablename__ = "plugin_media"

    id = Column(String, primary_key=True, default=gen_uuid)
    plugin_id = Column(String, ForeignKey("plugins.id"), nullable=False, index=True)
    kind = Column(String, nullable=False)  # icon | cover | screenshot
    sha256 = Column(String, nullable=False)
    content_type = Column(String, default="image/png")
    caption = Column(String, default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(Integer, default=now_ts)


class LunaInstall(Base):
    """A Luna instance enrolled via the handshake (see routers/luna_link.py).

    enroll: Luna sends its vault-stored install_id → we issue tenant secret +
    a short-lived link code. link: a signed-in web user submits the code →
    install binds to the user and the user becomes certified. sync: Luna
    pushes its installed-plugin list, HMAC-signed with the secret.
    """
    __tablename__ = "luna_installs"

    id = Column(String, primary_key=True, default=gen_uuid)
    install_id = Column(String, unique=True, nullable=False, index=True)
    secret = Column(String, nullable=False)  # HMAC key, server-generated
    user_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    luna_name = Column(String, default="")
    luna_version = Column(String, default="")
    base_url = Column(String, nullable=True)  # for settings deep-links, when known
    installed = Column(JSON, default=list)  # [{"name","version","settings":bool}]
    link_code = Column(String, nullable=True, index=True)
    link_code_expires = Column(Integer, default=0)
    created_at = Column(Integer, default=now_ts)
    linked_at = Column(Integer, nullable=True)
    last_sync_at = Column(Integer, nullable=True)


class UsageEvent(Base):
    """Metering from day one — captured always, billed later."""
    __tablename__ = "usage_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    org_id = Column(String, ForeignKey("orgs.id"), nullable=False)
    marketplace_id = Column(String, ForeignKey("marketplaces.id"), nullable=True)
    event_type = Column(String, nullable=False)  # publish, download, agent_pull
    plugin_name = Column(String, nullable=True)
    timestamp = Column(Integer, default=now_ts)
    metadata_ = Column("metadata", JSON, default=dict)
