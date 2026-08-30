// 089 (build/operate): conversation kind + state, plugin-local.
//
// The pinned @luna core this build inlines predates 089 — its
// ConversationSummary has no `kind`/`state`, api has no setConversationState,
// and the shared /api/events subscription doesn't carry conversation.* topics.
// Everything 089 needs therefore lives HERE (types, helpers, PATCH, SSE) so
// the plugin never imports symbols the pinned tree doesn't export.
import { fetchEventSource } from '@microsoft/fetch-event-source'
import { getToken } from '@luna/lib/api'

/** Reverse-proxy base path (e.g. "/a/my-agent"); empty when standalone. */
const BASE = (window as unknown as { __LUNA_BASE?: string }).__LUNA_BASE || ''

export type ConversationKind = 'building' | 'ops'

/** The 089 fields a conversation row MAY carry (older cores omit them). */
export interface ConvMeta {
  kind?: string | null
  state?: string | null
}

export interface StateOption {
  value: string
  label: string
  /** 014: ONE dim explanation line under the label in the state menu. */
  desc: string
}

// The full state vocabulary, per kind. Labels are the human words the
// picker shows; values are what the server stores and broadcasts.
export const STATE_OPTIONS: Record<ConversationKind, StateOption[]> = {
  building: [
    { value: 'planning', label: 'Planning', desc: 'Think and plan — research and notes, no building yet.' },
    { value: 'building', label: 'Building', desc: 'Full build — create and change playbooks, schedules, files.' },
  ],
  ops: [
    { value: 'identify', label: 'Identify', desc: "Diagnose Luna's own playbooks and plugins — report, change nothing." },
    { value: 'fix_approve', label: 'Fix & wait for approval', desc: "Fix Luna's own playbooks and plugins; each fix waits for your approval." },
    { value: 'fix_publish', label: 'Fix & publish', desc: "Fix Luna's own playbooks and plugins, publish without waiting." },
  ],
}

// 015 (ops only): what THIS agent works on. The state menu's one-liners
// alone read as if the fixes were about the user's data — they are not: the
// operations agent maintains Luna herself. Shown in the menu's "Details"
// expander (per option) and in the capability line's tooltip (current state).
export const OPS_AGENT_INTRO =
  "This agent maintains Luna herself — her own playbooks, plugins, connectors and schedules. Not your data."

export interface OpsStateDetail {
  /** What the agent may touch in this state. */
  can: string[]
  /** What it will NOT do in this state. */
  wont: string
}

export const OPS_STATE_DETAILS: Record<string, OpsStateDetail> = {
  identify: {
    can: [
      'Reads playbooks, plugins, connectors, schedules and run logs',
      'Reports what is broken and proposes the fix, here in this chat',
    ],
    wont: 'Changes nothing — no edits, no installs, no publishing.',
  },
  fix_approve: {
    can: [
      'Edits playbooks, plugin settings, connectors and schedules',
      'Stages every fix as an approval card — nothing lands until you say yes',
    ],
    wont: 'Never publishes or installs on its own.',
  },
  fix_publish: {
    can: [
      'Edits playbooks, plugin settings, connectors and schedules',
      'Publishes, installs and upgrades plugins right away',
    ],
    wont: 'Waits for nobody — every fix goes live as soon as it is made.',
  },
}

// 014 (ops only): the amber capability line next to the model selector —
// the bottom line of what the current state permits.
export const OPS_CAPABILITY: Record<string, string> = {
  identify: 'diagnose only — no changes',
  fix_approve: 'fixes wait for your approval',
  fix_publish: 'fixes publish without approval',
}

// 014/015: the capability line's hover tooltip — one tight intro sentence of
// depth (ux_guidelines §6: the line alone carries the bottom line). The
// per-state can/won't bullets (OPS_STATE_DETAILS) render under it.
export const OPS_CAPABILITY_TOOLTIP =
  'This is the Operations chat: the agent watches and repairs Luna herself. ' +
  'The state selector in the message box sets what it may change; ' +
  'background reports land here in every state — the state only controls what the agent may change.'

export const DEFAULT_STATE: Record<ConversationKind, string> = {
  building: 'building',
  ops: 'identify',
}

/** Kind of a conversation row — anything that isn't 'ops' is 'building'. */
export function convKind(c: ConvMeta | null | undefined): ConversationKind {
  return c?.kind === 'ops' ? 'ops' : 'building'
}

/** Effective state of a row: its own when valid for its kind, else the default. */
export function convState(c: ConvMeta | null | undefined): string {
  const k = convKind(c)
  const s = c?.state
  return s && STATE_OPTIONS[k].some((o) => o.value === s) ? s : DEFAULT_STATE[k]
}

/** Ops conversations pin to the top; relative order inside each group is kept. */
export function sortOpsFirst<T extends ConvMeta>(list: T[]): T[] {
  const ops: T[] = []
  const rest: T[] = []
  for (const c of list) (convKind(c) === 'ops' ? ops : rest).push(c)
  return ops.length ? [...ops, ...rest] : list
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  const t = getToken()
  if (t) h['Authorization'] = `Bearer ${t}`
  return h
}

/** PATCH /api/conversations/{id} {"state": value}. Throws on non-2xx. */
export async function patchConversationState(id: string, state: string): Promise<void> {
  const r = await fetch(`${BASE}/api/conversations/${id}`, {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ state }),
  })
  if (!r.ok) throw new Error(`state update failed (${r.status})`)
}

export interface ConvStateEvent {
  conversation_id: string
  kind?: string
  state?: string
}

/**
 * Live conversation.state_changed feed. The pinned core's shared /api/events
 * stream doesn't subscribe to conversation.* topics, so this opens its own
 * (one per ChatPanel mount) with the same reconnect/backoff shape the core
 * uses. Returns an unsubscribe.
 */
export function subscribeConvStateEvents(
  onChanged: (ev: ConvStateEvent) => void,
): () => void {
  const ctrl = new AbortController()
  let retry = 1000
  const connect = () => {
    if (ctrl.signal.aborted) return
    void fetchEventSource(`${BASE}/api/events?topics=conversation.*`, {
      headers: authHeaders(),
      signal: ctrl.signal,
      openWhenHidden: true,
      onopen() {
        retry = 1000
        return Promise.resolve()
      },
      onmessage(ev) {
        if (ev.event !== 'conversation.state_changed') return
        try {
          const data = JSON.parse(ev.data) as ConvStateEvent
          if (data && data.conversation_id) onChanged(data)
        } catch {
          /* malformed frame — ignore */
        }
      },
      onerror(err) {
        retry = Math.min(retry * 2, 30_000)
        if (!ctrl.signal.aborted) setTimeout(connect, retry)
        throw err // stop fetchEventSource's own retry loop
      },
    }).catch(() => {
      /* the throw above lands here — reconnection is already scheduled, and
         an unhandled rejection must never escape (it fails test runs). */
    })
  }
  connect()
  return () => ctrl.abort()
}
