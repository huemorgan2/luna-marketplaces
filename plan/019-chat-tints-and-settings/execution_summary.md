# 019 — execution summary

Shipped plugin-chat-ui 0.26.0 (commit c0e3680, published to
marketplaces.com.ai official, sha256 c5009c80…fcc2cf). Everything is
client-side in chat-ui; no core change, no server persistence.

## What shipped

- `src/lib/chatTint.ts` (new): five static tint presets (amber, sky,
  emerald, violet, rose), each with a message-pane wash class, an
  assistant-bubble class, and a swatch color. `chatTint()` resolves the
  effective tint — the stored pick when one exists ('none' = explicit
  no-tint), otherwise amber for ops chats and no tint for everything else.
  Persistence is `localStorage['luna.chat.tint.<conversationId>']` with
  try/catch on both ends. `ChatTintContext` carries the active chat's tint
  to `Bubble` (a context, not a `renderTimeline` parameter — that exported
  signature is called positionally by many tests and stayed untouched).
- Sidebar `ConversationItem`: the 0.25.0 amber wash/title on ops rows is
  gone — ops rows now use exactly the generic row classes; the OPS chip is
  the only marker. (The 0.25.0 treatment read as "selected" when it wasn't.)
- The tint paints where the owner meant it: the messages scroll pane gets
  the faint wash (`data-testid="chat-scroll-pane"`), and PLAIN assistant
  bubbles get the bubble tint. User bubbles keep the luna identity;
  reflection (sky) and automation (violet) bubbles are untouched — their
  color is their meaning.
- `ChatSettingsDialog` (new, same modal anatomy as the delete confirm),
  opened from the "…" menu's single "Chat settings" item: name input + save
  (`api.renameConversation`), message-color swatch row (Default + 5 tints,
  applies immediately), and a Delete danger row that reuses the existing
  confirm dialog. The delete row is absent for ops chats (server 403s
  DELETE). The old inline rename input and the Rename/Delete menu items are
  deleted; the menu is now Copy conversation / Copy agent context / Chat
  settings.
- Header title: shows the CHAT's title (fallback "New conversation"), not
  the agent's name; avatar and ops/debug chips unchanged. When no
  conversation list sits beside the chat (`compact` and not mobile), the
  title becomes a chevron pulldown listing all chats — ops pinned first,
  active checked — that switches conversations.

## Verification

- vitest: 125/125 green (8 new in `019-chat-tints.test.tsx`: tint defaults
  and persistence, Default-swatch clearing the ops amber, tint isolation
  from user/reflection bubbles, dialog rename/delete, header title,
  compact switcher; 089 sidebar/delete tests rewritten for the generic row
  and the settings-dialog delete). tsc + vite build clean; all tint classes
  confirmed present in the built `ui/chat.css`.
- QA Luna 8767 (core 0.92.012): upgraded in place 0.25.0 → 0.26.0 via the
  marketplace route, artifact sha matched. CDP (driver-owned tab): ops row
  generic both inactive and selected (no amber anywhere in the row class),
  header shows the conversation's title, menu has only Chat settings (no
  Rename/Delete), dialog shows the prefilled name + 6 swatches, delete row
  present on a building chat and absent on the ops chat, picking Sky tinted
  the building pane live, the ops pane carries the amber wash by default
  with 25 tinted bot bubbles, and the Amber swatch shows as selected in the
  ops chat's dialog.

## Notes

- Tints are per-browser by design (owner: core need not support any of
  this) — a second browser shows the defaults.
- The compact-mode chat switcher is unit-tested; the QA tenant renders the
  full sidebar layout, so CDP exercised the plain-title path.
