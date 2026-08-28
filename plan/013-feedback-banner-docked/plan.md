# 013 — Dock the agent-feedback banner to the composer

## Problem
The 011 banner ("Is the agent doing a good job?") is sticky at the top of the
message scroll area. It floats over the transcript and overlaps messages —
wrong place. Roy: it must attach to the TOP of the composer, seamlessly.

## Design
- Move the banner out of the scroll container. Render it inside `Composer`,
  directly above the input box, via a new `banner?: ReactNode` prop
  (state stays in ChatPanel — Composer just gets a slot).
- Restyle `AgentFeedbackBanner` as a docked tab:
  - a little narrower than the composer box (`px-6` inset wrapper, `w-fit`
    centered) so it sits on the flat segment of the box's top edge, clear of
    the `rounded-2xl` corners;
  - rounded TOP corners only, side + top borders, **no bottom border**;
  - solid `bg-ink-900`, `-mb-px` + `relative z-10` so it paints over the
    composer's top border in the attachment segment — no dark seam line
    between the widget and the message box (the "dark border above the
    message area" goes away where they join).

## Steps
1. Restyle `AgentFeedbackBanner` (ChatPanel.tsx).
2. Add `banner` prop to `Composer`; render above the box inside `max-w-3xl`.
3. In ChatPanel, move the conditional banner render from the scroll container
   into the `Composer` call.
4. `vitest run` — 011 tests must stay green.
5. Bump 0.15.0 → 0.16.0 (luna-plugin.toml + ui-src/package.json), build
   (`npm run build` → ui/), commit, push (huemorgan2), publish to
   marketplaces.com.ai.
