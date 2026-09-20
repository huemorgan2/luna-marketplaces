# 024 — Chat UI main integration report

Plan: [024](../../plan/024-auto-main-integration/PLAN.md).

Fetched upstream main `5397b31` with chat-ui 0.30.2 before merging. Resolved version metadata to **0.30.3** and rebuilt both assets from combined sources against Luna **0.92.058**. The core submodule pin is updated to that exact merged revision. All existing collapsed summary, receipt and credential-wake changes are preserved; the composer gains AUTO and persisted manual opt-out.

## Verification

- Clean `npm ci`, then full `npm test`: **141 passed across 21 files**.
- `npm run build`: **passed** (TypeScript and Vite).
- [Browser walkthrough](../022-auto-model-selection/dojo-scenarios.md): **passed** on desktop and mobile; [results](../022-auto-model-selection/browser-results.json), [desktop](../022-auto-model-selection/shots/auto-desktop.png), [mobile](../022-auto-model-selection/shots/auto-mobile.png).
- Final source diff against current main changes the composer selection wiring, without reverting the current summary/credential behavior.
- Manifest, package.json and package-lock.json all identify 0.30.3. Dependency versions remain those from upstream main.

Browser responses were controlled fixtures; no live model routing or production session was used. Installed Playwright drove a visible browser because Playwright MCP is unavailable. Dependency installation reported three audit findings from the unchanged upstream dependency set. The merge check against the feature branch encountered two pre-existing EOF whitespace warnings in upstream Playbooks files; the final diff against main is clean and those files were not edited.

The source is prepared for main. Plugin publication, hosted plugin pin updates and deployment are separate actions. Uncommitted expanded-model work in the original marketplace directory is preserved outside this merge.
