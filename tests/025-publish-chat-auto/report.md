# 025 — Chat UI 0.30.3 publication report

Plan: [025](../../plan/025-publish-chat-auto/PLAN.md). Verification time: 2026-09-20 10:31:39 UTC.

**Published:** [Chat UI 0.30.3](https://marketplaces.com.ai/browse/official/plugin/plugin-chat-ui).

- Render deploy `dep-danrbcbtqb8s73csncog` is **live** at exact source commit `3ecc933d914555f18512b4176a7fca59773dc617`.
- The [official registry](https://marketplaces.com.ai/mp/official/index.json) now reports **0.30.3**, replacing 0.30.2 as the current Chat UI entry.
- [Downloaded artifact](https://marketplaces.com.ai/mp/official/plugins/plugin-chat-ui/0.30.3/artifact.zip) matches the clean local package byte-for-byte: **121,727 bytes**, SHA-256 `7ea3545f2f7848d255fec3c1ff148ba6e53b792f47e5b40bca42393c6d25f1a2`.
- All five archive files match the local package hashes. TOML and runtime manifests agree on 0.30.3. The built JavaScript includes AUTO; development sources, dependencies and credentials are excluded.
- The catalog still contains the same **35 plugins**. All **34 other plugins** retain their prior version and hash.
- The public plugin detail page returns HTTP 200.

[Machine-readable evidence](release-verification.json) includes the source/core commits, deploy ID, artifact URL and file hashes. Local packaging was deterministic. The runtime artifact delta from the previously deployed service revision was limited to Chat UI. Existing plan 024 tests/build/browser results apply to these exact unchanged source bytes; no new model calls were made.

Publication makes the update available in the marketplace. It does not install the update into existing agents, change hosted image pins, deploy Luna core, or provision the Jev key. AUTO requires the matching core (0.92.058) and server-side Jev configuration; older cores retain manual picker behavior.
