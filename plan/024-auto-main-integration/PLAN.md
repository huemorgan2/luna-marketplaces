# 024 — Integrate AUTO with chat-ui main

**Produces version:** 0.30.3. Explicitly authorized: pull current code, merge to main, bump and push.

## Context

AUTO was based on 0.29.3 while main had reached 0.30.2. Preserve the collapsed tool summary, unified timeline receipts and removal of client credential auto-continuation while adding AUTO.

## Architecture impact

ALIGNED: existing shared core UI and model API contract. Companion Luna plan 118 produces 0.92.058.

## Goals

Bring AUTO onto current main, use a new version, rebuild against merged core, verify both new and upstream UI behaviors, and push main.

## Approach

Merge origin/main in the isolated AUTO checkout; resolve version metadata to 0.30.3; regenerate bundles; run full plugin tests plus the AUTO browser walkthrough; commit and push main without rewriting history.

## Data / API contract

Keep core's persisted auto/manual mode, mode-only AUTO writes, and old-core feature detection.

## Risks

The original marketplace workspace has unrelated local edits: preserve them. Generated artifacts cannot be resolved by picking an older version. This is a source merge, not marketplace publication or production deployment.

## Acceptance criteria

Remote main includes latest upstream and AUTO, all plugin versions agree on 0.30.3, rebuilt assets pass checks, and the result is recorded.

## Verification

Full plugin Vitest suite, TypeScript/Vite production build, AUTO/manual persistence and reload checks on desktop/mobile, and final remote SHA verification.
