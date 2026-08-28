# 013 — execution summary

Shipped plugin-chat-ui 0.16.0 (2026-08-28).

- `AgentFeedbackBanner` restyled as a docked tab: `w-fit` centered inside a
  `px-6` inset (clear of the box's `rounded-2xl` corners), `rounded-t-lg`,
  side + top borders only, solid `bg-ink-900`, `-mb-px` + `z-10` so it paints
  over the composer's top border — no dark seam where they join.
- Render moved from the message scroll container (was sticky top, overlapped
  the transcript) into a new `banner?: ReactNode` slot on `Composer`,
  rendered directly above the input box. State/handlers stay in ChatPanel.
- Tests: 96/96 green (011 banner tests untouched). Built `ui/chat.js`.
- Version 0.15.0 → 0.16.0 (luna-plugin.toml + ui-src/package.json).
