# 018 — Readable approval cards (rebuild against luna 0.91)

**Parent:** luna-plugins `plans/012-readable-ops-approvals/PLAN.md`
**Depends on:** luna core ≥ 0.91.000 merged (plan 094: `presentation` field + upgraded shared `InlineApprovalCard`).

## Purpose

plugin-chat-ui renders approval cards from luna core's shared sources (`@luna/views/InlineApprovalCard`, `@luna/lib/approvalGroups` — not vendored; `ui-src/tsconfig.json:16` resolves into `../../../luna/ui/src`). Core plan 094 adds plain-English presentation rendering (eyebrow → headline → explanation → collapsed diff). This plan ships that to the hosted chat surface.

## Steps

1. Update the in-repo `luna/` checkout to the merged 0.91.000 core (the `@luna` alias source).
2. Verify `ChatPanel.tsx` needs no changes (props of `InlineApprovalCard` are unchanged: `{reqs, decided, agentName}`); adjust only if the 094 card added optional props worth passing.
3. `npm run build` in `marketplace-src/plugin_chat_ui/ui-src`; run vitest + the chat-ui gate (`tools/chat-ui-gate.mjs`).
4. Bump plugin version `0.22.0` → `0.23.0` (all stamps: `luna-plugin.toml` + in-code manifest if present).
5. Manual check on QA Luna: ops conversation shows presentation cards (headline/explanation/collapsed diff) and legacy cards unchanged.
6. Push (huemorgan2), publish to marketplaces.com.ai.

## Risks

- The `@luna` tree pulls new bare deps → pin in `ui-src/package.json` per the existing react-pinning comment in `vite.config.ts`.
- Markdown rendering inside the card must reuse the pinned `react-markdown` instance to avoid a second react.
