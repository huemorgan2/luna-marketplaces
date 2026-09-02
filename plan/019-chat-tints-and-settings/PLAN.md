# 019 — chat tints + per-chat settings (plugin-chat-ui 0.26.0)

Owner order (2026-09-02), after seeing 0.25.0:

1. The sidebar Operations row still reads as selected — the 0.25.0 faint
   amber wash + amber title landed on the WRONG surface. Make the ops row
   identical to every other row (text color included); keep only the OPS tag.
2. The "very faint yellow tint" belongs on the ops CHAT AREA itself (the
   message pane), and the bot's chat bubbles in the ops chat get a slight
   yellow tint too.
3. Generalize: tinting is a per-chat quality. Every chat's "…" header menu
   gets a settings area with (a) message color — a choice of tint colors,
   (b) chat rename, (c) the delete option moved in there.
4. chat-ui only — no core support needed or added.
5. (added mid-plan) The chat header shows the CHAT's name, not the agent's
   name. And when the conversation sidebar is collapsed, the header title
   grows an arrow that opens a pulldown listing the other chats — picking
   one switches to it.

## Design

### Tint model (client-side only)

New `src/lib/chatTint.ts`:

- `CHAT_TINTS`: static presets (Tailwind needs literal class strings) —
  `amber`, `sky`, `emerald`, `violet`, `rose`. Each carries:
  - `area`: very faint wash for the message pane (e.g. `bg-amber-500/[0.05]`),
  - `bubble`: the assistant-bubble treatment (e.g.
    `bg-amber-950/45 border-amber-500/15`), matching the anatomy of the
    existing reflection (sky) / automation (violet) bubbles,
  - `swatch`: the picker dot color.
- Persistence: `localStorage['luna.chat.tint.<conversationId>']` — a tint id
  or `'none'` (explicit "no tint"). Not synced to the server; per-browser,
  same as the old state-details toggle. try/catch on both ends.
- `chatTint(convId, kind)`: stored value when valid; UNSET ops chats default
  to `amber` (that is how the owner's "yellow ops chat" ships), everything
  else defaults to no tint. `setChatTint(convId, id|null)`.

### Where the tint paints

- The messages scroll pane gets the `area` class (ChatPanel `chatArea`).
- Plain assistant bubbles (not user, not reflection, not automation) get the
  `bubble` class via a `ChatTintContext` (React context provided around the
  chat area; `Bubble` consumes it). Context, not a `renderTimeline`
  parameter — the exported signature stays untouched.
- Sidebar `ConversationItem`: ops styling drops back to the generic row
  (identical classes to building rows); only the OPS chip remains. The amber
  header "ops" chip stays.

### Settings dialog

`ChatSettingsDialog` (modal, same anatomy as `DeleteConversationDialog`):

- Name — text input + save (existing `api.renameConversation`).
- Message color — swatch row: Default + the five tints; clicking applies
  immediately (write localStorage, live re-render).
- Delete — danger row at the bottom (existing confirm dialog + DELETE);
  absent for ops chats (server 403s them).

Header "…" menu becomes: Copy conversation / Copy agent context / Chat
settings. The inline rename input and the separate Rename/Delete menu items
move into the dialog.

### Header title = chat name (+ switcher when the sidebar is hidden)

- The header's title slot renders the ACTIVE CHAT's title (fallback "New
  conversation"), not the agent's name. The avatar stays; the agent's name
  lives in the sidebar/app chrome, not here.
- When the conversation list is not visible next to the chat (collapsed
  sidebar, or the compact embed that never shows one), the title becomes a
  button with a chevron: an upward/downward menu (same anatomy as the other
  header menus) listing all chats — ops pinned first, active checked —
  clicking one switches conversations. With the sidebar visible the title is
  plain text (the sidebar already does the switching).

## Not doing

- No server persistence of tints (owner: "the core need not support any of
  these"). A second browser shows default tints — accepted.
- No tint on user bubbles (they keep the luna-purple identity), no tint on
  reflection/automation bubbles (their color IS their meaning).

## Verification

- vitest: update 089 sidebar/delete tests, new 019 test file (tint defaults,
  swatch picking + persistence, dialog rename/delete, ops delete hidden).
- tsc + vite build; QA Luna 8767 via CDP: ops row generic, ops pane + bot
  bubbles amber-tinted, settings dialog drives rename/tint/delete.
- Ship 0.26.0 → push huemorgan2/luna-marketplaces → publish official.
