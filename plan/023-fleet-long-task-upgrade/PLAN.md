# 023 — Publish verified long-task plugins for the fleet

## Context

The fleet's official marketplace currently serves Playbooks 0.57.2, Scheduler 0.8.0, and DB 0.1.0. The merged Luna/Dojo long-task work tested Playbooks 0.57.16, Scheduler 0.8.3, and DB 0.1.1 with the complete default plugin profile. Marketplace regression testing also exposed existing manifest drift in MCP 0.2.0 and Web Access 0.3.0; their already-tested source branches contain immutable follow-up versions 0.2.1 and 0.3.1. The user authorized merging, publishing, and upgrading the entire fleet.

## Goals

- Publish those three tested packages and the version-correct MCP and Web Access packages as immutable versions in the official marketplace.
- Keep every other current official plugin unchanged and available.
- Supply artifact hashes for a complete fleet image pin set, including DB and the current TypeSafe plugin.
- Verify marketplace catalog and downloadable artifact hashes before any fleet image build.

## Non-goals

- Rewrite plugin code or change the package versions that were measured.
- Publish unrelated dirty UI work from the original marketplace checkout.
- Remove historical plugin versions, artifacts, tenant data, or marketplace records.

## Approach

1. Work from a clean worktree at current `origin/main` and copy the committed package directories from the merged Playbooks, Scheduler, DB, MCP, and Web Access repositories into `marketplace-src/`.
2. Check each package's `luna-plugin.toml` against its runtime manifest and source commit; reject caches, test outputs, and secrets.
3. Run marketplace packaging/seed tests locally, and confirm every new zip has one package root and stable SHA-256.
4. Merge the marketplace worktree by fast-forward into remote `main`, let Render deploy it, and verify all five entries and artifacts over HTTPS.
5. Record the exact published versions and hashes for the Luna service image pinning step.

## Data/API contract

The official catalog remains at `/mp/official/index.json`. The five entries must expose `plugins/{name}/{version}/artifact.zip` and the catalog `sha256` must match the downloaded bytes. Existing versions remain immutable and accessible.

## Risks

- A previously published name/version with different bytes is immutable; stop and investigate instead of overwriting it.
- The marketplace has newer unrelated Chat UI work and an uncommitted local checkout; use the isolated worktree to preserve both.
- The final image profile has more plugins than the Dojo 18-plugin research profile; validate the full frozen pin set before fleet promotion.

## Acceptance criteria

- Playbooks 0.57.16, Scheduler 0.8.3, DB 0.1.1, MCP 0.2.1, and Web Access 0.3.1 appear in the live official index.
- All five artifact bytes match their live index hashes and package manifests.
- No other official plugin entry is lost or downgraded.
- The source is committed and pushed to marketplace `main`, with a factual test report in `tests/023-fleet-long-task-upgrade/report.md`.

## Verification

Run the marketplace service tests and source/version checks, then fetch the live index and each new artifact. Compare the catalog before/after set and hashes. Use those hashes in the control-plane plugin pins and verify the fleet image separately.
