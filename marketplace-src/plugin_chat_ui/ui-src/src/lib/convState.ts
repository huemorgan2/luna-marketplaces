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
}

// The full state vocabulary, per kind. Labels are the human words the
// pulldown shows; values are what the server stores and broadcasts.
export const STATE_OPTIONS: Record<ConversationKind, StateOption[]> = {
  building: [
    { value: 'planning', label: 'Planning' },
    { value: 'building', label: 'Building' },
  ],
  ops: [
    { value: 'identify', label: 'Identify' },
    { value: 'fix_approve', label: 'Fix & wait for approval' },
    { value: 'fix_publish', label: 'Fix & publish' },
  ],
}

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
