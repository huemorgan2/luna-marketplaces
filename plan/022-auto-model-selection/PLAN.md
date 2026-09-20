# 022 — AUTO model selection in the chat picker

**Produces version:** plugin-chat-ui 0.30.0. User explicitly authorized plan and execution on 2026-09-20.

## Context

The owner wants AUTO as the default model selection. Core implementation is Luna `plans/110-auto-model-selection/PLAN.md`. This isolated checkout preserves unrelated edits in the marketplace working directory.

## Architecture impact

ALIGNED: the chat plugin imports shared core API/components and persists selection through the existing model endpoint. Core documents its new selector in `vision/architecture2.md` §14a (this marketplace has no separate architecture2 document).

## Goals

Show AUTO, save auto/manual mode, preserve the fallback chain, restore selection on reload, and support an older core by hiding AUTO when the new field is absent.

## Non-Goals

Production publication, unrelated operations-channel edits, or changes to model routing within the frontend.

## Approach

Wire the existing composer picker to core's optional `selection` field and mode-aware API; selecting AUTO sends a mode-only write, selecting a model sets manual mode. Keep optimistic update/rollback and caps-only semantics. Build against the changed core sources in the local test checkout. Bump the plugin to 0.30.0 and rebuild its bundle.

## Data / API contract

GET `/api/models` reasoning entry includes `selection: auto|manual`. PUT accepts that field; no literal AUTO enters a provider chain. Missing selection on older cores means existing manual behavior.

## Risks

Mixed core/plugin versions and stale optimistic state. Feature-detect support, reload after save, and roll back on failed writes. A local browser fixture must distinguish mocked API evidence from live routing evaluation.

## Acceptance criteria

AUTO appears first and is selected for backend auto mode. Manual choice disables AUTO; reselecting AUTO preserves the configured fallback chain. Reload retains each setting. Caps-only writes keep mode. UI builds and tests pass with a browser screenshot.

## Verification

Focused Vitest integration tests for the composer, `npm run build`, and controlled local browser interactions. The session has no Playwright MCP tool; use installed Playwright browser automation as the available equivalent and record this in the report. No production browser or real remote-control session is used.
