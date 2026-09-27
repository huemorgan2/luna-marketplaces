# 026 — luna-service-mobile 0.1.0

Owner request (roy, 2026-09-27): a marketplace plugin, not Luna core, called luna-service-mobile, baked
into every hosted Luna, feeding the Luna iPhone app's chat feed. The full design and fleet rollout are in
luna-service `plans/082-luna-service-mobile/PLAN.md`; this plan covers the plugin and its publication.

## What it does

Subscribes (background) to Luna's bus: `message.received`, `message.created`, `agent.turn.started/ended`,
`approval.requested/decided/orphan_decided`, `conversation.state_changed`. Enqueues only. One sender task
resolves items into small feed events (reading Luna's `conversations` / `messages` with plain SQL for
titles and the rows a finished turn wrote), batches them and POSTs
`{LUNA_GATEWAY_URL}/api/agent/chats/events` with `LUNA_GATEWAY_TOKEN`. Snapshot of the 100 most recent
conversations on server ready. Idle without the gateway env. No tools, routes or tables.

## Acceptance

- Unit tests in `tests/026-luna-service-mobile` pass.
- Loaded by a real Luna (image plugin-set dir) and, against a local luna-service, a chat message produces
  user message, turn.started, assistant message and turn.ended in the feed; a restart re-sends nothing twice.
- Published to official with index sha256 equal to the served artifact; other entries unchanged.
