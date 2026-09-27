# 026 — report

**Unit tests:** `pytest tests/026-luna-service-mobile -o asyncio_mode=auto` → 8 passed (reader: turn rows,
user/muted/approval/conversation events, snapshot idempotency, bad input; sender: batching, retry with
backoff, drop on 401, bounded queue, background loop). `service/tests/test_manifest_version_sync.py` passes.

**Real Luna, end to end (2026-09-27, Luna 887e2fb / 0.92.058):** Luna served locally with
`LUNA_PLUGIN_SET_DIR` holding this package, `LUNA_GATEWAY_URL` pointing at luna-service (branch
`chat-feed`) with a real gateway token. First run caught `category = "connectivity"` being rejected by
Luna's PluginManifest (fixed to `connectors`). Then: plugin loaded, boot snapshot posted the Operations
chat; a chat message produced `message(user)`, `turn.started`, `message(assistant)`, `turn.ended` in
`chat_events`, and `/api/feed` showed the chat with title, preview, unread 1, working false. After a Luna
restart the snapshot added no duplicate message events.

**Package:** deterministic sha256 `bd8e62f71d9386a49a05463a8fb5a11ec2ccef528592c55ccd2e4466872a274f`,
four files under `luna_service_mobile/`.
