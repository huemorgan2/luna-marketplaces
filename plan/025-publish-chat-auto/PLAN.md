# 025 — Publish chat-ui 0.30.3

**Produces version:** none (publish the already committed and tested 0.30.3 artifact). Explicitly authorized by the owner's “do it” on 2026-09-20.

## Context

Main 3ecc933 contains chat-ui 0.30.3 and pins Luna 887e2fb (0.92.058), but marketplaces.com.ai still serves chat-ui 0.30.2. This first-party plugin is seeded from the marketplace service's committed source on startup.

## Architecture impact

ALIGNED: existing deterministic packaging, immutable versions and official registry publication through the Render marketplace service. No new API, schema or runtime behavior.

## Goals

Publish the exact tested 0.30.3 bytes, verify the official index and downloaded artifact hash, preserve all other published plugin entries, and record release evidence.

## Non-Goals

Deploying Luna agent images, modifying fleet pins/secrets, changing plugin code, or publishing unrelated local work.

## Approach

1. Record the live index and deployment; package chat-ui from clean committed sources using the same deterministic packager as the seeder.
2. Verify version/runtime manifest agreement, one archive root, expected files, and absence of development sources. Confirm the deployment delta from the currently live revision only changes chat-ui runtime artifacts.
3. Trigger Render service srv-d8m7nct8nd3s73dofrm0 at exact commit 3ecc933d914555f18512b4176a7fca59773dc617. Existing authenticated Render CLI access is available.
4. Wait for the deployment, fetch the official index and artifact, compare SHA-256 and every packaged file, and compare all other plugin versions/hashes with the baseline.
5. Record results and commit the documentation to main without changing the published artifact.

## Data / API contract

Official registry: https://marketplaces.com.ai/mp/official/index.json. Artifact: plugins/plugin-chat-ui/0.30.3/artifact.zip. Published name/version bytes are immutable.

## Risks

Wrong source revision, stale artifact, or collateral catalog changes. Pin the deployment commit and compare served bytes with the locally verified package and baseline catalog. Existing clients without AUTO support retain manual behavior.

## Acceptance criteria

Render deploy is live at the specified revision; index reports 0.30.3; artifact hash and embedded files match the clean source package; all other plugin versions/hashes are unchanged.

## Verification

Reuse completed plan 024 tests/build/browser evidence because artifact sources are unchanged. Run deterministic package/runtime-manifest checks and live HTTP artifact verification. Hosted Luna rollout remains a separate operation.
