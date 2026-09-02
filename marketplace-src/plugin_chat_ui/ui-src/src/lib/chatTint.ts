// 019: per-chat message-color tints. A chat's tint paints a very faint wash
// on the message pane and a slight color on the agent's plain bubbles. Chosen
// per chat in the header's Chat settings dialog; stored per-browser only —
// the server knows nothing about tints.
import { createContext } from 'react'

export type TintId = 'amber' | 'sky' | 'emerald' | 'violet' | 'rose'

export interface ChatTintDef {
  id: TintId
  label: string
  /** Very faint wash for the whole message pane. */
  area: string
  /** The plain assistant-bubble treatment (same anatomy as the reflection/automation bubbles). */
  bubble: string
  /** The settings-dialog swatch dot. */
  swatch: string
}

// Tailwind needs literal class strings — a static map, never string-built.
export const CHAT_TINTS: Record<TintId, ChatTintDef> = {
  amber: {
    id: 'amber', label: 'Amber',
    area: 'bg-amber-500/[0.05]',
    bubble: 'bg-amber-950/45 border-amber-500/15',
    swatch: 'bg-amber-400',
  },
  sky: {
    id: 'sky', label: 'Sky',
    area: 'bg-sky-500/[0.05]',
    bubble: 'bg-sky-950/45 border-sky-500/15',
    swatch: 'bg-sky-400',
  },
  emerald: {
    id: 'emerald', label: 'Emerald',
    area: 'bg-emerald-500/[0.05]',
    bubble: 'bg-emerald-950/45 border-emerald-500/15',
    swatch: 'bg-emerald-400',
  },
  violet: {
    id: 'violet', label: 'Violet',
    area: 'bg-violet-500/[0.05]',
    bubble: 'bg-violet-950/45 border-violet-500/15',
    swatch: 'bg-violet-400',
  },
  rose: {
    id: 'rose', label: 'Rose',
    area: 'bg-rose-500/[0.05]',
    bubble: 'bg-rose-950/45 border-rose-500/15',
    swatch: 'bg-rose-400',
  },
}

const storageKey = (convId: string) => `luna.chat.tint.${convId}`

/**
 * The chat's effective tint: the stored choice when one exists ('none' is an
 * explicit no-tint), otherwise the kind default — ops chats start amber,
 * everything else untinted.
 */
export function chatTint(convId: string | null, kind: string | null): ChatTintDef | null {
  if (!convId) return null
  let stored: string | null = null
  try {
    stored = localStorage.getItem(storageKey(convId))
  } catch {
    /* storage unavailable — fall through to the defaults */
  }
  if (stored === 'none') return null
  if (stored && stored in CHAT_TINTS) return CHAT_TINTS[stored as TintId]
  return kind === 'ops' ? CHAT_TINTS.amber : null
}

/** Persist a pick; `null` = the Default swatch (explicit no-tint). */
export function setChatTint(convId: string, id: TintId | null) {
  try {
    localStorage.setItem(storageKey(convId), id ?? 'none')
  } catch {
    /* storage unavailable — the pick just doesn't survive a reload */
  }
}

// Provided around the chat area with the ACTIVE chat's tint; `Bubble` consumes
// it. A context, not a renderTimeline parameter — the exported renderTimeline
// signature is called positionally by many tests and stays untouched.
export const ChatTintContext = createContext<ChatTintDef | null>(null)
