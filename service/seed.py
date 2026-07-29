"""Seed the marketplace with sample plugins for demo/testing.

Demo-only: sample plugins, demo users (certified), and demo reviews. The
official marketplace is seeded from marketplace-src/ at startup (seed_core)
and never gets fabricated reviews."""

import asyncio
import hashlib
import io
import json
import uuid
import zipfile
from pathlib import Path

from app.database import init_db, async_session
from app.auth import hash_password
from app.models.db import Marketplace, Org, OrgMember, Plugin, PluginVersion, Review, User, now_ts

SAMPLE_PLUGINS = [
    {
        "name": "google-ads",
        "category": "connectivity",
        "namespace": "luna-official",
        "version": "2.1.0",
        "description": "Connect Luna to Google Ads — check campaigns, adjust bids, pause/resume, generate performance reports",
        "license": "Commercial",
        "tags": ["advertising", "google", "marketing", "campaigns", "analytics"],
        "readme": "# Google Ads Plugin\n\nFull Google Ads integration for Luna. Check campaign performance, adjust bids intelligently, pause underperforming campaigns, and generate weekly reports.\n\n## Features\n- Real-time campaign metrics\n- Smart bid adjustment with safety limits\n- Automatic performance alerts\n- Weekly report generation",
        "permissions": {
            "tools": [
                {"name": "check_campaign", "policy": "auto_approve", "description": "Check performance metrics for a campaign"},
                {"name": "adjust_bids", "policy": "prompt_first_time_only", "description": "Change bid amount for a campaign"},
                {"name": "pause_campaign", "policy": "prompt_always", "description": "Pause an active campaign"},
                {"name": "generate_report", "policy": "auto_approve", "description": "Generate performance report"},
            ],
            "egress_hosts": ["ads.googleapis.com"],
            "vault_access": True,
            "ui_iframe": True,
            "settings_tab": True,
        },
    },
    {
        "name": "slack-channel",
        "category": "communication",
        "namespace": "luna-official",
        "version": "1.5.0",
        "description": "Slack integration — Luna communicates through workspaces, channels, DMs, and threads with full approval button support",
        "license": "MIT",
        "tags": ["communication", "slack", "channels", "messaging"],
        "readme": "# Slack Channel Plugin\n\nConnect Luna to your Slack workspace. Luna can receive messages, respond in threads, and render approval buttons directly in Slack.\n\n## Setup\n1. Create a Slack app\n2. Add Bot Token and Signing Secret\n3. Configure event subscriptions",
        "permissions": {
            "tools": [
                {"name": "send_slack_message", "policy": "prompt_first_time_only", "description": "Send a message in a Slack channel"},
                {"name": "read_channel_history", "policy": "auto_approve", "description": "Read recent messages from a channel"},
            ],
            "egress_hosts": ["slack.com", "api.slack.com"],
            "vault_access": True,
            "ui_iframe": False,
            "settings_tab": True,
        },
    },
    {
        "name": "charts",
        "category": "media",
        "namespace": "luna-official",
        "version": "1.2.0",
        "description": "Interactive Chart.js charts rendered inline in Luna's chat — bar, line, pie, doughnut, and radar charts",
        "license": "MIT",
        "tags": ["visualization", "charts", "data", "ui"],
        "readme": "# Charts Plugin\n\nRender beautiful interactive charts directly in Luna's chat. Supports bar, line, pie, doughnut, and radar chart types.\n\n## Usage\n\nAsk Luna to visualize data and it will render an interactive Chart.js chart inline.",
        "permissions": {
            "tools": [
                {"name": "render_chart", "policy": "auto_approve", "description": "Render an interactive chart in chat"},
            ],
            "egress_hosts": [],
            "vault_access": False,
            "ui_iframe": False,
            "settings_tab": False,
        },
    },
    {
        "name": "web-access",
        "category": "knowledge",
        "namespace": "luna-official",
        "version": "1.0.3",
        "description": "Give Luna the ability to browse the web — fetch pages, extract content, search, and summarize URLs",
        "license": "MIT",
        "tags": ["web", "browsing", "research", "content"],
        "readme": "# Web Access Plugin\n\nAllows Luna to browse the web, fetch page content, and extract structured information from URLs.\n\n## Tools\n- `fetch_url` — Retrieve and parse a webpage\n- `web_search` — Search the web via DuckDuckGo\n- `extract_content` — Extract main content from a URL",
        "permissions": {
            "tools": [
                {"name": "fetch_url", "policy": "auto_approve", "description": "Fetch and parse a webpage"},
                {"name": "web_search", "policy": "auto_approve", "description": "Search the web"},
                {"name": "extract_content", "policy": "auto_approve", "description": "Extract main content from a URL"},
            ],
            "egress_hosts": ["*"],
            "vault_access": False,
            "ui_iframe": False,
            "settings_tab": True,
        },
    },
    {
        "name": "scheduler",
        "category": "automation",
        "namespace": "luna-official",
        "version": "2.0.0",
        "description": "Schedule recurring and one-off tasks — cron jobs, reminders, periodic checks, and time-based automation",
        "license": "MIT",
        "tags": ["automation", "scheduling", "cron", "tasks", "reminders"],
        "readme": "# Scheduler Plugin\n\nGive Luna the ability to schedule tasks for the future. Supports cron expressions, one-off reminders, and periodic health checks.\n\n## Usage\n\n`/schedule every day at 9am check campaign performance`",
        "permissions": {
            "tools": [
                {"name": "schedule_task", "policy": "prompt_first_time_only", "description": "Schedule a new task"},
                {"name": "list_schedules", "policy": "auto_approve", "description": "List all scheduled tasks"},
                {"name": "cancel_schedule", "policy": "prompt_always", "description": "Cancel a scheduled task"},
            ],
            "egress_hosts": [],
            "vault_access": False,
            "ui_iframe": True,
            "settings_tab": True,
        },
    },
    {
        "name": "memory-pinecone",
        "category": "knowledge",
        "namespace": "luna-official",
        "version": "1.0.0",
        "description": "Alternative memory provider using Pinecone for high-scale vector search — drop-in replacement for default pgvector memory",
        "license": "Commercial",
        "tags": ["memory", "vector-search", "pinecone", "provider", "enterprise"],
        "readme": "# Pinecone Memory Provider\n\nA drop-in replacement for Luna's default pgvector memory plugin. Uses Pinecone for vector storage and search, optimized for large-scale deployments.\n\n## When to use\n- More than 100k memory facts\n- Need for cross-region replication\n- Existing Pinecone infrastructure",
        "permissions": {
            "tools": [],
            "egress_hosts": ["*.pinecone.io"],
            "vault_access": True,
            "ui_iframe": False,
            "settings_tab": True,
            "provider": "memory",
        },
    },
    {
        "name": "hubspot-crm",
        "category": "connectivity",
        "namespace": "acme-vendor",
        "version": "3.2.1",
        "description": "HubSpot CRM integration — manage contacts, deals, and pipeline from within Luna conversations",
        "license": "Commercial",
        "tags": ["crm", "hubspot", "sales", "contacts", "deals"],
        "readme": "# HubSpot CRM Plugin\n\nFull HubSpot CRM integration. Luna can create contacts, update deals, move pipeline stages, and generate sales reports.\n\n## Features\n- Contact management\n- Deal pipeline tracking\n- Activity logging\n- Sales reporting",
        "permissions": {
            "tools": [
                {"name": "create_contact", "policy": "prompt_first_time_only", "description": "Create a new CRM contact"},
                {"name": "update_deal", "policy": "prompt_first_time_only", "description": "Update deal stage or properties"},
                {"name": "search_contacts", "policy": "auto_approve", "description": "Search contacts by name or email"},
                {"name": "log_activity", "policy": "auto_approve", "description": "Log a call, email, or meeting"},
                {"name": "sales_report", "policy": "auto_approve", "description": "Generate sales pipeline report"},
            ],
            "egress_hosts": ["api.hubapi.com"],
            "vault_access": True,
            "ui_iframe": True,
            "settings_tab": True,
        },
    },
    {
        "name": "email-campaigns",
        "category": "communication",
        "namespace": "acme-vendor",
        "version": "1.4.0",
        "description": "Design, schedule, and send email campaigns. Integrates with Mailgun and tracks open/click metrics",
        "license": "Commercial",
        "tags": ["email", "marketing", "campaigns", "automation", "mailgun"],
        "readme": "# Email Campaigns Plugin\n\nLuna can draft, schedule, and send email campaigns. Tracks opens, clicks, and bounces. Uses Mailgun for delivery.\n\n## Safety\nAll sends require explicit approval — no accidental mass emails.",
        "permissions": {
            "tools": [
                {"name": "draft_campaign", "policy": "auto_approve", "description": "Draft an email campaign"},
                {"name": "send_campaign", "policy": "prompt_always", "description": "Send a campaign to recipients"},
                {"name": "campaign_stats", "policy": "auto_approve", "description": "Check campaign open/click stats"},
            ],
            "egress_hosts": ["api.mailgun.net"],
            "vault_access": True,
            "ui_iframe": True,
            "settings_tab": True,
        },
    },
]


async def seed():
    await init_db()
    async with async_session() as db:
        # Create demo user
        user = User(
            id=str(uuid.uuid4()),
            email="demo@marketplaces.com.ai",
            username="demo",
            password_hash=hash_password("demo123"),
        )
        db.add(user)

        # Create org
        org = Org(id=str(uuid.uuid4()), name="Luna Official", slug="luna-official")
        db.add(org)
        db.add(OrgMember(id=str(uuid.uuid4()), org_id=org.id, user_id=user.id, role="owner"))

        # Create marketplace
        mp = Marketplace(
            id=str(uuid.uuid4()),
            org_id=org.id,
            name="Official Luna Marketplace",
            slug="official",
            description="The official Luna plugin marketplace — curated, verified, trusted",
            visibility="public",
        )
        db.add(mp)

        # Add plugins
        seeded_plugins = []
        for pdata in SAMPLE_PLUGINS:
            permissions = pdata.get("permissions", {})
            tools = permissions.get("tools", [])
            plugin = Plugin(
                id=str(uuid.uuid4()),
                marketplace_id=mp.id,
                name=pdata["name"],
                namespace=pdata["namespace"],
                description=pdata["description"],
                readme=pdata.get("readme", ""),
                tags=pdata.get("tags", []),
                license=pdata.get("license", "MIT"),
                latest_version=pdata["version"],
                source_url=f"https://github.com/luna-plugins/{pdata['name']}",
                requires_tools=len(tools) > 0,
                category=pdata.get("category"),
                requires_ui_iframe=permissions.get("ui_iframe", False),
                requires_settings_tab=permissions.get("settings_tab", False),
                requires_vault_access=permissions.get("vault_access", False),
                requires_egress=permissions.get("egress_hosts", []),
                tool_count=len(tools),
                tool_policies=tools,
                download_count=hash(pdata["name"]) % 500 + 50,
            )
            db.add(plugin)
            seeded_plugins.append(plugin)

            # Create version
            manifest_data = {k: v for k, v in pdata.items()}
            manifest_data["compat"] = {"sdk": "^1.0", "requires": {"tools": ">=1"}}
            pv = PluginVersion(
                id=str(uuid.uuid4()),
                plugin_id=plugin.id,
                version=pdata["version"],
                artifact_hash=hashlib.sha256(pdata["name"].encode()).hexdigest(),
                manifest_hash=hashlib.sha256(json.dumps(manifest_data).encode()).hexdigest(),
                manifest_data=manifest_data,
                sdk_compat="^1.0",
                capabilities_required={"tools": ">=1"},
            )
            db.add(pv)

        # Demo reviewers (certified — as if they linked a Luna install) + reviews.
        reviewers = []
        for uname in ("mira", "jonas", "petra"):
            r = User(
                id=str(uuid.uuid4()),
                email=f"{uname}@marketplaces.com.ai",
                username=uname,
                password_hash=hash_password("demo123"),
                certified_at=now_ts(),
            )
            db.add(r)
            reviewers.append(r)

        DEMO_REVIEWS = [
            (5, "Just works", "Set it up in two minutes and Luna handled the rest. Exactly what I hoped for."),
            (4, "Solid, small rough edges", "Does what it says. Approval prompts are sensible. Docs could be a bit deeper."),
            (5, "Part of my daily flow now", "I use this every day — reliable and fast, and the settings are clear."),
        ]
        review_count = 0
        for i, plugin in enumerate(seeded_plugins):
            n = (i % 3) + 1  # 1..3 reviews per plugin, varied
            total = 0
            for j in range(n):
                rating, title, body = DEMO_REVIEWS[(i + j) % len(DEMO_REVIEWS)]
                db.add(Review(
                    id=str(uuid.uuid4()),
                    plugin_id=plugin.id,
                    user_id=reviewers[j].id,
                    rating=rating,
                    title=title,
                    body=body,
                    plugin_version=plugin.latest_version or "",
                    helpful_count=(i + j) % 4,
                ))
                total += rating
                review_count += 1
            plugin.rating_count = n
            plugin.rating_average = round(total / n, 2)

        await db.commit()
        print(f"Seeded: {1 + len(reviewers)} users, 1 org, 1 marketplace, {len(SAMPLE_PLUGINS)} plugins, {review_count} reviews")


if __name__ == "__main__":
    asyncio.run(seed())
