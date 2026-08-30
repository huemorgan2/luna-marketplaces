import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform, type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Plus, Send, Trash2, Loader2, MoreHorizontal, MoreVertical, Pencil, Check, X, Copy, ChevronDown, ChevronLeft, Info, Square, Clock, Wrench, Paperclip, FileText, Brain, Zap, Hand } from 'lucide-react'
import { cn } from '@luna/lib/cn'
import { agentName, useIdentity } from '@luna/lib/identityStore'
import {
  api, cardAction, getToken, sendMessageStream, continueConversation, startOnboardingStream, subscribeApprovalEvents,
  queueMessage, stopTurn, getTurnStatus, attachTurnStream, uploadAttachment,
  type AttachmentInfo,
  type ConversationSummary, type Identity, type Message as ApiMessage,
  type SecretRequestSummary, type ContextStatus,
  type ModelChain, type CatalogEntry, type PlanTask, type PlanSnapshotTask,
} from '@luna/lib/api'
import { cachedGet, cachedSet } from '@luna/lib/cache'
import { CHAT_BRIDGE_EVENT, type ChatBridgeMessage } from '@luna/lib/pluginBridge'
import { inlineEmbedAssets, forceEagerEmbedImages } from '@luna/lib/embedAssets'
import { reorderChainHead } from '@luna/lib/modelChain'
import {
  fetchDebugEventsBackfill,
  formatDebugLine,
  formatDebugLineCompact,
  renderCleanTranscript,
  renderDebugTranscript,
  subscribeAllEvents,
  type BubbleMessage,
  type DebugEvent,
} from '@luna/lib/debug'
import { useDebugMode } from '@luna/lib/useDebugMode'
import { InlineApprovalCard, humanizeTool } from '@luna/views/InlineApprovalCard'
import { InlineSecretForm } from './InlineSecretForm'
import { TaskPlanCard } from './TaskPlanCard'
import { LiveRunList, applyLiveEvent, isLiveEvent, type LiveEvent, type LiveRun } from './LiveRunBlock'
import { composerWidgets } from '../lib/composerWidgets'
import { matchesFrustration, alertSuppressed, suppressAlert } from '../lib/chatAlerts'
import {
  STATE_OPTIONS, DEFAULT_STATE, OPS_CAPABILITY, OPS_CAPABILITY_TOOLTIP,
  OPS_AGENT_INTRO, OPS_STATE_DETAILS,
  convKind, convState, sortOpsFirst,
  patchConversationState, subscribeConvStateEvents,
  type ConversationKind, type ConvMeta,
} from '../lib/convState'
import { groupApprovals, isAutoApproved, type ApprovalRecord } from '@luna/lib/approvalGroups'
import { AgentAvatar } from '@luna/components/AgentAvatar'
import { ModelPickerMenu } from '@luna/components/ModelPickerMenu'
import { StackPane } from '@luna/components/StackPane'
import { isMobileViewport, useViewport } from '@luna/lib/viewport'
import { useRegisterMobileDetail } from '@luna/lib/mobileNav'

/** Reverse-proxy base path (e.g. "/a/my-agent"); empty when standalone. */
const BASE = (window as any).__LUNA_BASE || ''

/**
 * Resolve an asset URL for chat markdown. Auth is carried by the session cookie
 * (see luna/auth/cookie.py), so we only need to make the URL resolve correctly:
 * root-absolute same-origin paths get the reverse-proxy base prefix so a
 * `<img src="/api/p/...">` works behind `/a/<slug>` too. External/data/blob URLs
 * are returned untouched.
 */
function resolveAssetUrl(src: string): string {
  if (!src) return src
  if (/^(data:|https?:\/\/|blob:)/i.test(src)) return src
  if (BASE && src.startsWith('/') && !src.startsWith(BASE + '/')) return BASE + src
  return src
}

/**
 * Fetch an authenticated same-origin plugin asset (referenced inside an embed)
 * in the PARENT — where the bearer token and session cookie work — and return it
 * as a `data:` URL the sandboxed iframe can display. `null` on any failure so the
 * caller degrades to the original embed. See `lib/embedAssets.ts` and plan
 * 008.961 for why this is required (the sandbox can't send credentials).
 */
async function fetchEmbedAssetAsDataUrl(rawPath: string): Promise<string | null> {
  try {
    const idx = rawPath.indexOf('api/p/')
    if (idx < 0) return null
    const url = (BASE || '') + '/' + rawPath.slice(idx)
    const tok = getToken()
    const r = await fetch(url, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: tok ? { Authorization: `Bearer ${tok}` } : {},
    })
    if (!r.ok) return null
    const blob = await r.blob()
    return await new Promise<string>((resolve, reject) => {
      const fr = new FileReader()
      fr.onload = () => resolve(fr.result as string)
      fr.onerror = () => reject(fr.error)
      fr.readAsDataURL(blob)
    })
  } catch {
    return null
  }
}

/**
 * Renders a plugin chat embed. Plugin embeds run in a `sandbox="allow-scripts allow-downloads"`
 * iframe (opaque origin) that cannot send the session cookie, so an
 * authenticated same-origin asset (e.g. a generated image) 401s and shows as
 * broken. We pre-fetch such assets in the parent and inline them as `data:` URLs
 * before the HTML reaches the sandbox. A brief placeholder avoids a flash of the
 * broken image; on failure we fall back to the original (eager) HTML.
 */
// 056: hard ceiling for embed-requested heights — the page scrolls, the card
// never grows unbounded from a hostile/buggy embed. 1400 leaves headroom for
// tall legitimate cards (e.g. a game scene with banner + 10 options) that at
// 900 got an internal scrollbar.
const EMBED_HEIGHT_CAP = 1400

function PluginEmbed({
  html,
  asIframe,
  card,
  source,
  conversationId,
  messageId,
}: {
  html: string
  asIframe: boolean
  card?: boolean
  // 057: card-action bridge — the plugin that posted this card (message
  // extra.source); actions are confined to that plugin's route prefix.
  source?: string | null
  conversationId?: string
  messageId?: string
}) {
  const [prepared, setPrepared] = useState<string | null>(null)
  // 056: auto-height — the embed may post {type:"luna:embed:height", height}
  // on load/resize; matched to THIS iframe's contentWindow so a foreign frame
  // can't resize it. Null → legacy fixed bounds.
  const [height, setHeight] = useState<number | null>(null)
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  useEffect(() => {
    let alive = true
    setPrepared(null)
    inlineEmbedAssets(html, fetchEmbedAssetAsDataUrl)
      .then((out) => alive && setPrepared(out))
      .catch(() => alive && setPrepared(forceEagerEmbedImages(html)))
    return () => {
      alive = false
    }
  }, [html])
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      const d = e.data as { type?: string; height?: unknown } | null
      if (!d || d.type !== 'luna:embed:height') return
      if (!frameRef.current || e.source !== frameRef.current.contentWindow) return
      const n = Number(d.height)
      if (Number.isFinite(n) && n > 0) setHeight(Math.min(Math.ceil(n), EMBED_HEIGHT_CAP))
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])
  // 057: card-action bridge. A card iframe (sandboxed, no token, opaque
  // origin) posts {type:"luna:card:action", nonce, path, body}; the shell
  // performs the HTTP call with the user's auth and replies with
  // {type:"luna:card:result", nonce, ok, status, body}. Confused-deputy
  // guard: only card rows participate, and the path must live under the
  // posting plugin's own route prefix (/api/p/<source>/).
  useEffect(() => {
    if (!card || !source) return
    const prefix = `/api/p/${source}/`
    const onMsg = (e: MessageEvent) => {
      const d = e.data as {
        type?: string
        nonce?: unknown
        path?: unknown
        body?: unknown
      } | null
      if (!d || d.type !== 'luna:card:action') return
      const frame = frameRef.current
      if (!frame || e.source !== frame.contentWindow) return
      const nonce = typeof d.nonce === 'string' ? d.nonce : ''
      const reply = (ok: boolean, status: number, body: unknown) =>
        frame.contentWindow?.postMessage(
          { type: 'luna:card:result', nonce, ok, status, body },
          '*',
        )
      const path = typeof d.path === 'string' ? d.path : ''
      if (!path.startsWith(prefix) || path.includes('..')) {
        reply(false, 0, { detail: 'path outside plugin route prefix' })
        return
      }
      cardAction(path, {
        ...(typeof d.body === 'object' && d.body !== null ? d.body : {}),
        conversation_id: conversationId || null,
        message_id: messageId || null,
      })
        .then((r) => reply(r.ok, r.status, r.body))
        .catch((err: Error) => reply(false, 0, { detail: err.message }))
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [card, source, conversationId, messageId])

  if (prepared === null) {
    return (
      <div
        className="mt-2 rounded-xl bg-ink-900/60 border border-white/5 grid place-items-center text-ink-500 text-xs"
        style={{ minHeight: 120 }}
      >
        <span className="dots">loading</span>
      </div>
    )
  }
  if (asIframe) {
    return (
      <iframe
        ref={frameRef}
        data-testid={card ? 'card-embed' : 'bubble-embed'}
        srcDoc={prepared}
        className={cn('w-full border-0 rounded-xl', !card && 'mt-2')}
        style={height !== null ? { height } : { minHeight: 300, maxHeight: 500 }}
        sandbox="allow-scripts allow-downloads"
      />
    )
  }
  return (
    <div
      className="mt-2 rounded-xl overflow-hidden"
      dangerouslySetInnerHTML={{ __html: prepared }}
    />
  )
}

/** Allow inline data:image/* (react-markdown strips data: by default); defer to
 *  the library default for everything else (which still blocks javascript: etc). */
function chatUrlTransform(url: string): string {
  if (/^data:image\//i.test(url)) return url
  return defaultUrlTransform(url)
}

/** Fenced code blocks get a hover-revealed copy button. Same clipboard strategy
 *  as the transcript copy: clipboard API first, execCommand fallback (the API
 *  requires document focus; execCommand doesn't). */
const PreWithCopy: Components['pre'] = ({ node: _node, children, ...props }) => {
  const preRef = useRef<HTMLPreElement>(null)
  const [copied, setCopied] = useState(false)
  async function copyBlock() {
    const text = preRef.current?.textContent ?? ''
    if (!text) return
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      } catch {
        ok = false
      }
    }
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1200)
    }
  }
  return (
    <div className="relative group/code">
      <pre ref={preRef} {...props}>{children}</pre>
      <button
        type="button"
        onClick={copyBlock}
        aria-label="Copy code"
        title={copied ? 'Copied!' : 'Copy'}
        className={cn(
          'absolute top-2 right-2 p-1.5 rounded-md border border-white/10 bg-black/40 backdrop-blur-sm transition-opacity',
          copied
            ? 'opacity-100 text-emerald-400'
            : 'opacity-0 group-hover/code:opacity-100 focus-visible:opacity-100 text-ink-200 hover:text-white',
        )}
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      </button>
    </div>
  )
}

/** Markdown renderers for assistant chat: images load eagerly (lazy breaks inside
 *  sandboxed embeds and adds nothing for an immediately-visible chat image) and
 *  degrade to alt text on error instead of a raw broken-image icon. */
const chatMarkdownComponents: Components = {
  pre: PreWithCopy,
  img: ({ src, alt, ...props }) => (
    <img
      {...props}
      src={resolveAssetUrl(typeof src === 'string' ? src : '')}
      alt={alt || ''}
      loading="eager"
      referrerPolicy="no-referrer"
      className="max-w-full h-auto rounded-lg my-2 border border-white/5"
      onError={(e) => {
        const el = e.currentTarget
        const fallback = document.createElement('span')
        fallback.className = 'text-ink-500 text-xs italic'
        fallback.textContent = alt ? `🖼️ ${alt} (image unavailable)` : '🖼️ image unavailable'
        el.replaceWith(fallback)
      }}
    />
  ),
  a: ({ href, children, ...props }) => {
    const resolved = resolveAssetUrl(typeof href === 'string' ? href : '')
    const external = /^https?:\/\//i.test(resolved) && !resolved.startsWith(window.location.origin)
    return (
      <a {...props} href={resolved} {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
        {children}
      </a>
    )
  },
  // A table wider than the bubble scrolls inside its own wrapper — without it
  // the table stretches the message column and the whole chat pans sideways.
  table: ({ node: _node, children, ...props }) => (
    <div className="table-scroll">
      <table {...props}>{children}</table>
    </div>
  ),
}

interface UIMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  pending?: boolean
  created_at?: string
  embed_html?: string
  embed_iframe?: string
  source?: string | null
  // 007.016: inline non-blocking notice (e.g. automatic model fallback).
  notice?: { from?: string; to?: string; reason?: string }
  // 039: the billing gateway refused the turn (402) — action-required banner.
  policy_block?: { code?: string; message?: string; retryable?: boolean }
  // 080: kind === 'turn_error' — the turn failed with a typed, honest notice
  // (live SSE `turn_error`/`error`, or a persisted row's extra). `content` is
  // the plain-language message; `retryable` drives the Retry button.
  error_code?: string | null
  retryable?: boolean | null
  // 008.9: a centered muted status line (e.g. "Stopping agent…").
  system_line?: boolean
  // 031: a user message sent while the agent was working — shown as a normal
  // bubble immediately (WhatsApp-style), dimmed until the server acks the queue.
  queued?: boolean
  // 008.994: a muted system→agent message — renders as a collapsible dark-grey
  // line (`▸ title`) the owner can expand to read `content`.
  kind?: string | null
  title?: string | null
  // 026: kind === "task_plan" — a finished plan anchored permanently in the
  // timeline; `tasks` is the frozen checklist, `title` the plan intent.
  plan_status?: 'completed' | 'superseded' | 'dismissed' | null
  tasks?: PlanSnapshotTask[] | null
  // 008.95: user-message attachments (image thumbnails / file chips).
  attachments?: AttachmentInfo[] | null
  // 071: the agent's live reasoning trace. While the bubble is pending with no
  // answer text yet it renders expanded and ticking; once the answer starts (or
  // on reload) it folds into a clickable "Thought Ns" pill. reasoning_ms is
  // wall-ms spent thinking (÷1000 = the pill's N).
  reasoning?: string
  reasoning_ms?: number
  // 078: live tool runs (ui.live.* frames) attached to this bubble while its
  // turn streams — code shown at once, output as it happens, folds on close.
  // Not persisted; a reload renders the tool's embed_iframe card instead.
  live_runs?: LiveRun[]
}

// 008.95: a file staged in the composer, uploading (or awaiting a
// conversation to upload into) until send() binds it to a message.
interface StagedAttachment {
  id: string
  file: File
  status: 'staged' | 'uploading' | 'ready' | 'error'
  info?: AttachmentInfo
  error?: string
  previewUrl?: string
}

// 005.82-fixes2 item B: per-conversation composer draft persistence.
const draftKey = (id: string | null) => `luna.draft.${id ?? 'new'}`

// 012: the live-turn chrome for ONE conversation. Every conversation gets its
// own record so a running turn's stream callbacks can never paint another
// chat's composer or timeline (chat-switch isolation).
interface TurnUI {
  streaming: boolean
  toolNames: string[]
  subagent: { label: string; text: string; status: 'running' | 'done' | 'aborted' } | null
  waitLine: { until: number; reason: string; resuming: boolean } | null
  stopStage: 0 | 1 | 2
  condensing: boolean
}
const EMPTY_TURN: TurnUI = {
  streaming: false,
  toolNames: [],
  subagent: null,
  waitLine: null,
  stopStage: 0,
  condensing: false,
}
const NO_MESSAGES: UIMessage[] = []

// 089: a conversation row as this UI handles it — the core summary plus the
// build/operate fields newer servers return (kind/state). Older servers omit
// them and every helper defaults to a plain building conversation.
type ConvRow = ConversationSummary & ConvMeta

// 089: one tool call as tracked by the wrench progress chip. Lives OUTSIDE
// TurnUI on purpose: TurnUI.toolNames is wiped when the stream closes, while
// the chip's final "🔧 n tools" summary must survive the turn.
interface ToolLogEntry {
  name: string
  status: 'running' | 'done' | 'error'
}
const NO_TOOL_LOG: ToolLogEntry[] = []

export function ChatPanel({
  identity,
  onIdentityChange,
  needsOnboarding = false,
  onOnboardingComplete,
  initialConversationId,
  onConversationChange,
  compact = false,
  mobileVisible = true,
  onUiEvent,
}: {
  identity: Identity | null
  onIdentityChange?: (i: Identity) => void
  needsOnboarding?: boolean
  onOnboardingComplete?: () => void
  initialConversationId?: string
  onConversationChange?: (id: string | null) => void
  compact?: boolean
  /** 057: false while the mobile shell shows another area (chat stays mounted but must not claim the nav). */
  mobileVisible?: boolean
  onUiEvent?: (evt: { type: string; [key: string]: any }) => void
}) {
  // 008.995: seed from the last-known list so warm boots paint instantly;
  // refreshConversations() below replaces it (stale-while-revalidate).
  // 089: ops conversations pin to the top of the list, everywhere the list
  // is (re)built — cache seed, refresh, create, live state events.
  const [conversations, setConversations] = useState<ConvRow[]>(
    () => sortOpsFirst(cachedGet<ConvRow[]>('conversations') ?? []),
  )
  const [activeId, setActiveIdRaw] = useState<string | null>(initialConversationId || null)

  const setActiveId = useCallback((id: string | null) => {
    setActiveIdRaw(id)
    onConversationChange?.(id)
  }, [onConversationChange])
  // 012: messages are keyed by conversation id so a turn that keeps streaming
  // after the user switches chats writes into ITS OWN timeline, never the one
  // on screen. Key '' = the pre-conversation (onboarding kickoff) state.
  const [messagesByConv, setMessagesByConv] = useState<Record<string, UIMessage[]>>({})
  const setConvMessages = useCallback(
    (convId: string | null, updater: UIMessage[] | ((m: UIMessage[]) => UIMessage[])) => {
      const key = convId ?? ''
      setMessagesByConv((prev) => {
        const cur = prev[key] ?? NO_MESSAGES
        const next = typeof updater === 'function' ? updater(cur) : updater
        return next === cur ? prev : { ...prev, [key]: next }
      })
    },
    [],
  )
  // Delta/live buffers are keyed by message id (globally unique), so their
  // flushes map over every loaded conversation — a chunk always lands on its
  // own bubble no matter which chat is on screen.
  const mapAllConvMessages = useCallback((mapper: (m: UIMessage[]) => UIMessage[]) => {
    setMessagesByConv((prev) => {
      let changed = false
      const next: Record<string, UIMessage[]> = {}
      for (const [k, v] of Object.entries(prev)) {
        const nv = mapper(v)
        next[k] = nv
        if (nv !== v) changed = true
      }
      return changed ? next : prev
    })
  }, [])
  const messages = messagesByConv[activeId ?? ''] ?? NO_MESSAGES
  const [input, setInput] = useState('')
  // 065: chat bridge — the Composer parks a focus() thunk here, and the
  // effect below routes shell-dispatched luna-chat actions into the composer.
  const composerFocusRef = useRef<(() => void) | null>(null)
  const sendRef = useRef<(t?: string) => Promise<void>>(async () => {})
  useEffect(() => {
    const onChat = (e: Event) => {
      const d = (e as CustomEvent<ChatBridgeMessage>).detail
      if (!d) return
      if (d.action === 'prefill') {
        setInput(d.text)
        composerFocusRef.current?.()
      } else if (d.action === 'focus') {
        composerFocusRef.current?.()
      } else if (d.action === 'send') {
        void sendRef.current(d.text)
      }
    }
    window.addEventListener(CHAT_BRIDGE_EVENT, onChat)
    return () => window.removeEventListener(CHAT_BRIDGE_EVENT, onChat)
  }, [])
  // 008.95: files staged in the composer (paste / drop / paperclip). Uploads
  // start immediately when a conversation exists; otherwise they run at send
  // time right after the conversation is created.
  const [staged, setStaged] = useState<StagedAttachment[]>([])
  const stagedRef = useRef<StagedAttachment[]>([])
  stagedRef.current = staged
  const uploadPromisesRef = useRef<Map<string, Promise<AttachmentInfo | null>>>(new Map())
  // 012: ALL live-turn chrome (streaming flag, tool wrenches, subagent line,
  // wait countdown, stop stage, condense lock) is keyed by conversation id —
  // switching chats must show THAT chat's turn state, not the last turn's.
  const [turns, setTurns] = useState<Record<string, TurnUI>>({})
  const patchTurn = useCallback(
    (convId: string | null, patch: Partial<TurnUI> | ((t: TurnUI) => Partial<TurnUI>)) => {
      const key = convId ?? ''
      setTurns((prev) => {
        const cur = prev[key] ?? EMPTY_TURN
        const p = typeof patch === 'function' ? patch(cur) : patch
        return { ...prev, [key]: { ...cur, ...p } }
      })
    },
    [],
  )
  // Synchronous mirror of which conversations have a live stream owned or
  // watched by this tab (state is async; guards need the truth NOW).
  const streamingConvsRef = useRef<Set<string>>(new Set())
  const setConvStreaming = useCallback((convId: string | null, on: boolean) => {
    const key = convId ?? ''
    if (on) streamingConvsRef.current.add(key)
    else streamingConvsRef.current.delete(key)
    patchTurn(key, { streaming: on })
  }, [patchTurn])
  // 089: per-conversation tool log behind the wrench progress chip. Reset at
  // the START of the next user turn (never at stream close) so the collapsed
  // "🔧 n tools" receipt persists after the turn ends.
  const [toolLog, setToolLog] = useState<Record<string, ToolLogEntry[]>>({})
  const resetToolLog = useCallback((convId: string | null) => {
    const key = convId ?? ''
    setToolLog((prev) => (prev[key]?.length ? { ...prev, [key]: [] } : prev))
  }, [])
  const logToolCalls = useCallback((convId: string | null, names: string[]) => {
    if (!names.length) return
    const key = convId ?? ''
    setToolLog((prev) => ({
      ...prev,
      [key]: [...(prev[key] ?? []), ...names.map((name) => ({ name, status: 'running' as const }))],
    }))
  }, [])
  const logToolResult = useCallback((convId: string | null, name: string, result: string) => {
    const key = convId ?? ''
    // Heuristic error accent: tool errors come back as prose, not a flag.
    const errored = /^\s*(error|⚠|❌|traceback)/i.test(result || '')
    setToolLog((prev) => {
      const cur = prev[key]
      if (!cur?.length) return prev
      const idx = cur.findIndex((e) => e.status === 'running' && e.name === name)
      if (idx < 0) return prev
      const next = [...cur]
      next[idx] = { ...next[idx], status: errored ? 'error' : 'done' }
      return { ...prev, [key]: next }
    })
  }, [])
  // A stream that closes mid-call must not leave phantom spinners in the log.
  const settleToolLog = useCallback((convId: string | null) => {
    const key = convId ?? ''
    setToolLog((prev) => {
      const cur = prev[key]
      if (!cur?.some((e) => e.status === 'running')) return prev
      return { ...prev, [key]: cur.map((e) => (e.status === 'running' ? { ...e, status: 'done' as const } : e)) }
    })
  }, [])
  // The active conversation's live-turn view — same names the render code and
  // composer always used.
  const turn = turns[activeId ?? ''] ?? EMPTY_TURN
  const streaming = turn.streaming
  const stopStage = turn.stopStage
  const toolNames = turn.toolNames
  const subagent = turn.subagent
  const waitLine = turn.waitLine
  const condensing = turn.condensing
  // The text that started the active turn — placed back in the composer on stop.
  const lastUserTextRef = useRef('')
  // Synchronous in-flight guard per conversation: `streaming` is a
  // render-closure value, so a second Enter while send() is still setting up
  // (frozen tab flushing queued events, or the createConversation await) reads
  // a stale `false` and would start a duplicate turn.
  const sendBusyConvsRef = useRef<Set<string>>(new Set())
  const [offline, setOffline] = useState(false)
  // Only show the list skeleton on a true cold start — if the cache seeded
  // the list above, there's nothing to wait for visually.
  const [loadingConvs, setLoadingConvs] = useState(() => cachedGet<ConversationSummary[]>('conversations') === null)
  const [loadingMessages, setLoadingMessages] = useState(false)
  // Transient tool activity / subagent shimmer / wait countdown all live in
  // the per-conversation `turns` record above (012).
  // 069: wait countdown ticker — runs only while the ACTIVE chat has a live
  // countdown (other chats' countdowns resync from ui.wait.tick frames).
  const [waitNow, setWaitNow] = useState(() => Date.now())
  useEffect(() => {
    if (!waitLine || waitLine.resuming) return
    const t = window.setInterval(() => setWaitNow(Date.now()), 500)
    return () => window.clearInterval(t)
  }, [waitLine])
  const clearWaitLine = useCallback((convId: string | null) => {
    patchTurn(convId, (t) => (t.waitLine ? { waitLine: null } : {}))
  }, [patchTurn])
  // 007.007: context-window fullness ring under the composer.
  const [contextStatus, setContextStatus] = useState<ContextStatus | null>(null)
  // 005.8-polish: inline approval cards. The dispatch gate suspends a risky
  // tool call and fires `approval.requested` on /api/events; we render a card
  // below the agent's prose and the chat stream resumes once it's decided.
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([])
  // 006.708: in-chat secret forms (vault). Same lifecycle as approvals:
  // live SSE + poll backfill, filtered by conversation at render time.
  const [secretReqs, setSecretReqs] = useState<SecretRequestSummary[]>([])
  // Agent task plan — the latest full snapshot. Live `ui.tasks` frames replace
  // it wholesale; an empty payload hides the card.
  // 045/phase05: seed from the last-known snapshot so a reload paints the card
  // immediately (stale-while-revalidate); the mount fetch below replaces it.
  const [planTasks, setPlanTasks] = useState<PlanTask[]>(
    () => cachedGet<{ tasks: PlanTask[]; created_at: string | null }>('plan-tasks')?.tasks ?? [],
  )
  // 026: when the plan started — anchors the live card chronologically in the
  // timeline at its creation point (it scrolls with the chat).
  const [planCreatedAt, setPlanCreatedAt] = useState<string | null>(
    () => cachedGet<{ tasks: PlanTask[]; created_at: string | null }>('plan-tasks')?.created_at ?? null,
  )
  // The plan's home conversation — the live card renders ONLY there, never in
  // other chats. Every ui.tasks frame and the backfill carry conversation_id.
  const [planConversationId, setPlanConversationId] = useState<string | null>(
    () => cachedGet<{ conversation_id?: string | null }>('plan-tasks')?.conversation_id ?? null,
  )
  // 043: server-truth "a turn is live for this plan" — the ONLY non-local
  // signal allowed to animate the card. Persisted in_progress rows are not.
  const [planTurnActive, setPlanTurnActive] = useState(false)
  // 043: the active card can fold to a one-line pill; remembered per plan.
  const [planCollapsed, setPlanCollapsed] = useState(false)
  // 038/phase04: Resume was accepted but the agent hasn't visibly started —
  // the card shows a disabled "Resuming…" button instead of a dead Resume.
  // Set optimistically on click (and by `resuming` ui.tasks frames from other
  // tabs), cleared when work becomes visible or after a 60 s safety timeout.
  const [planResuming, setPlanResuming] = useState(false)
  const planResumingTimer = useRef<number | null>(null)
  const clearPlanResuming = useCallback(() => {
    setPlanResuming(false)
    if (planResumingTimer.current !== null) {
      window.clearTimeout(planResumingTimer.current)
      planResumingTimer.current = null
    }
  }, [])
  const scrollRef = useRef<HTMLDivElement>(null)
  const isAtBottomRef = useRef(true)
  const [showNewMessages, setShowNewMessages] = useState(false)
  const kickedOff = useRef(false)
  // 005.82-fixes2 item B: persist the composer draft per conversation. Keep a
  // ref of the active id so the persist effect writes under the right key even
  // as activeId changes (the ref-update effect below runs first).
  const activeIdRef = useRef<string | null>(activeId)
  useEffect(() => { activeIdRef.current = activeId }, [activeId])

  // 011: agent-behaviour feedback banner — exists only when plugin-feedback
  // is installed & enabled (its entry shows up in /api/ui/plugins).
  const [hasFeedback, setHasFeedback] = useState(false)
  const [feedbackAlertConv, setFeedbackAlertConv] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    try {
      // Optional-call guard: absent in older libs and in test api mocks.
      api.uiPlugins?.()
        .then((entries) => {
          if (alive) setHasFeedback(entries.some((e) => e.name === 'plugin-feedback'))
        })
        .catch(() => { /* no registry → no banner; never blocks the composer */ })
    } catch { /* same: banner just stays off */ }
    return () => { alive = false }
  }, [])
  useEffect(() => {
    try {
      const key = draftKey(activeIdRef.current)
      if (input) localStorage.setItem(key, input)
      else localStorage.removeItem(key)
    } catch { /* localStorage unavailable */ }
  }, [input])

  // 036/phase02: live twin of the persisted ⏹ turn_stopped marker — a broken
  // turn (urgent message or stop) must never leave a silent void in the
  // timeline. On reload the DB marker row renders instead.
  const stopMarkerText = (reason: string) =>
    reason === 'injected'
      ? '⏹ operation stopped — interrupted by your message'
      : '⏹ operation stopped'

  // 008.9: append a centered muted status line (stop / inject acknowledgements).
  // 012: scoped — callers inside a stream pass the stream's conversation;
  // composer-local callers pass the active one.
  const addSystemLine = useCallback((convId: string | null, text: string) => {
    setConvMessages(convId, (m) => [
      ...m,
      {
        id: `sys-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
        role: 'system',
        content: text,
        system_line: true,
        created_at: new Date().toISOString(),
      },
    ])
  }, [setConvMessages])

  // 080: the turn failed with a typed notice. Any prose already streamed into
  // the pending bubble stays (finished, not pending); the failure itself is a
  // separate `turn_error` card — the same shape the server persists, so a
  // reload shows exactly what the live stream showed. Delta flushing is the
  // caller's job (it owns the flush refs).
  const applyTurnError = useCallback((convId: string | null, msgId: string, n: { code: string; message: string; retryable: boolean }) => {
    setConvMessages(convId, (m) => {
      const idx = m.findIndex((x) => x.id === msgId)
      const card: UIMessage = {
        id: `turn-error-${Date.now()}`,
        role: 'assistant',
        content: n.message,
        kind: 'turn_error',
        error_code: n.code,
        retryable: n.retryable,
        created_at: new Date().toISOString(),
      }
      if (idx < 0) return [...m, card]
      const cur = m[idx]
      if (!cur.content.trim() && !cur.reasoning) {
        // Empty pending bubble → becomes the card (keeps the row position/id).
        const copy = [...m]
        copy[idx] = { ...cur, ...card, id: cur.id, pending: false }
        return copy
      }
      const copy = m.map((x) => (x.id === msgId ? { ...x, pending: false } : x))
      copy.splice(idx + 1, 0, card)
      return copy
    })
  }, [setConvMessages])

  // 080: Retry on an error card resends the user message that preceded it.
  // Plain function (not memoized): it must see the current `messages` and the
  // current `send` closure.
  function retryAfterError(cardId: string) {
    const list = messages
    const idx = list.findIndex((x) => x.id === cardId)
    let text = ''
    for (let i = (idx < 0 ? list.length : idx) - 1; i >= 0; i--) {
      if (list[i].role === 'user' && list[i].content.trim()) { text = list[i].content; break }
    }
    if (!text) text = lastUserTextRef.current
    if (!text) return
    void send(text)
  }
  const retryAfterErrorRef = useRef(retryAfterError)
  retryAfterErrorRef.current = retryAfterError
  const onTurnErrorRetryCb = useCallback((cardId: string) => retryAfterErrorRef.current(cardId), [])

  // 007.011: track whether the user is scrolled to the bottom
  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    isAtBottomRef.current = atBottom
    if (atBottom) setShowNewMessages(false)
  }, [])

  // Agent task plan: intercept `ui.tasks` frames from the chat SSE stream
  // (each carries the FULL current plan); everything else flows up to Shell
  // (e.g. ui.navigate).
  const handleUiEvent = useCallback((convId: string | null, evt: { type: string; [key: string]: any }) => {
    // 049/phase02: subagent lifecycle → the one shimmer line. Never a log.
    // 012: scoped to the stream's conversation.
    if (evt.type === 'subagent.started') {
      const label = (evt.label as string) || 'agent'
      patchTurn(convId, { subagent: { label, text: `${label} working…`, status: 'running' } })
      return
    }
    if (evt.type === 'subagent.progress') {
      const label = (evt.label as string) || 'agent'
      patchTurn(convId, { subagent: { label, text: String(evt.text || '…'), status: 'running' } })
      return
    }
    if (evt.type === 'subagent.finished') {
      patchTurn(convId, (t) => {
        const label = (evt.label as string) || t.subagent?.label || 'agent'
        if (evt.aborted) {
          return { subagent: { label, text: `${label} stopped (${evt.abort_reason || 'aborted'})`, status: 'aborted' } }
        }
        const secs = Math.max(1, Math.round(((evt.duration_ms as number) || 0) / 1000))
        return { subagent: { label, text: `${label} · done · ${secs}s`, status: 'done' } }
      })
      return
    }
    // 069: wait countdown — started/tick set the clock anchor, finished
    // flips to "resuming…" (cleared by the next delta / turn end).
    if (evt.type === 'ui.wait.started') {
      patchTurn(convId, {
        waitLine: {
          until: Date.now() + Number(evt.seconds || 0) * 1000,
          reason: String(evt.reason || ''),
          resuming: false,
        },
      })
      return
    }
    if (evt.type === 'ui.wait.tick') {
      patchTurn(convId, (t) =>
        t.waitLine && !t.waitLine.resuming
          ? { waitLine: { ...t.waitLine, until: Date.now() + Number(evt.remaining || 0) * 1000 } }
          : {},
      )
      return
    }
    if (evt.type === 'ui.wait.finished') {
      patchTurn(convId, (t) => (t.waitLine ? { waitLine: { ...t.waitLine, resuming: true } } : {}))
      return
    }
    if (evt.type === 'ui.tasks') {
      const tasks = (evt.tasks as PlanTask[]) ?? []
      setPlanTasks(tasks)
      setPlanCreatedAt((evt.created_at as string | null) ?? null)
      setPlanConversationId((evt.conversation_id as string | null) ?? null)
      setPlanTurnActive(!!evt.turn_active)
      // 038/phase04: an accepted resume re-emits the plan with resuming: true
      // (flips every tab); visible work (in_progress) clears the state.
      if (evt.resuming) setPlanResuming(true)
      else if (tasks.some((t) => t.status === 'in_progress')) clearPlanResuming()
      return
    }
    onUiEvent?.(evt)
  }, [onUiEvent, clearPlanResuming, patchTurn])

  // 038/phase04: a running turn in this tab is visible work — stop showing
  // "Resuming…" the moment it starts.
  useEffect(() => {
    if (streaming) clearPlanResuming()
  }, [streaming, clearPlanResuming])
  // 069: the wait line is retired per-conversation in each stream's terminal
  // handler (012) — no global effect needed.

  // Agent task plan: backfill ONCE on mount, so a reload (or returning to the
  // chat) shows the plan without waiting for the next `ui.tasks` frame.
  // 045/phase05 (= 044 Bug 20): the endpoint is global (not per-conversation),
  // and keying this on activeId made the null→id flip on first load cancel its
  // own first response — the card only appeared on the SECOND fetch round.
  // Conversation switches stay fresh via the ui.tasks SSE frames + the global
  // onUiTasks stream; no refetch needed.
  useEffect(() => {
    let cancelled = false
    api.planTasks()
      .then((r) => {
        if (cancelled) return
        setPlanTasks(r.tasks ?? [])
        setPlanCreatedAt(r.created_at ?? null)
        setPlanConversationId(r.conversation_id ?? null)
        setPlanTurnActive(!!r.turn_active)
      })
      .catch(() => { /* plugin absent or endpoint down — no card */ })
    return () => { cancelled = true }
  }, [])

  // 045/phase05: write-through so the seed above always has the last snapshot.
  useEffect(() => {
    cachedSet('plan-tasks', { tasks: planTasks, created_at: planCreatedAt, conversation_id: planConversationId })
  }, [planTasks, planCreatedAt, planConversationId])

  // 043: while the card animates off a server-side turn (not this tab's
  // stream), re-poll so a crashed/vanished turn decays to a static card — the
  // registry reclaims dead slots, no frame ever announces their death.
  const planInProgress = planTasks.some((t) => t.status === 'in_progress')
  useEffect(() => {
    if (streaming || !planTurnActive || !planInProgress) return
    const t = window.setInterval(() => {
      void api.planTasks()
        .then((r) => {
          setPlanTasks(r.tasks ?? [])
          setPlanCreatedAt(r.created_at ?? null)
          setPlanConversationId(r.conversation_id ?? null)
          setPlanTurnActive(!!r.turn_active)
        })
        .catch(() => setPlanTurnActive(false))
    }, 30_000)
    return () => window.clearInterval(t)
  }, [streaming, planTurnActive, planInProgress])

  // 043: collapse is remembered per plan (keyed by its creation time) so SSE
  // refreshes and reloads keep the pill folded.
  const planKey = planCreatedAt ?? planTasks[0]?.id ?? null
  useEffect(() => {
    if (!planKey) return
    try {
      setPlanCollapsed(localStorage.getItem(`luna.plan.collapsed:${planKey}`) === '1')
    } catch { setPlanCollapsed(false) }
  }, [planKey])
  const togglePlanCollapsed = useCallback(() => {
    setPlanCollapsed((v) => {
      const next = !v
      try {
        if (planKey) {
          if (next) localStorage.setItem(`luna.plan.collapsed:${planKey}`, '1')
          else localStorage.removeItem(`luna.plan.collapsed:${planKey}`)
        }
      } catch { /* localStorage unavailable */ }
      return next
    })
  }, [planKey])

  // 045/phase05: batch streaming deltas per animation frame. One setMessages
  // per frame instead of one per SSE token — a fast model can emit hundreds of
  // tokens/s and each state set used to reconcile the whole timeline. The
  // buffer is keyed by message id so a mid-flush onNewMessage can't misroute
  // a chunk; terminal handlers call flushDeltasNow() so no token is ever lost.
  const deltaBufRef = useRef<Map<string, string>>(new Map())
  const deltaRafRef = useRef<number | null>(null)
  const flushDeltasNow = useCallback(() => {
    if (deltaRafRef.current !== null) {
      cancelAnimationFrame(deltaRafRef.current)
      deltaRafRef.current = null
    }
    const buf = deltaBufRef.current
    if (buf.size === 0) return
    deltaBufRef.current = new Map()
    mapAllConvMessages((m) =>
      m.some((msg) => buf.has(msg.id))
        ? m.map((msg) => {
            const add = buf.get(msg.id)
            return add ? { ...msg, content: msg.content + add } : msg
          })
        : m,
    )
  }, [mapAllConvMessages])
  const queueDelta = useCallback((convId: string | null, id: string, delta: string) => {
    clearWaitLine(convId) // 069: visible stream activity ends the wait line
    deltaBufRef.current.set(id, (deltaBufRef.current.get(id) ?? '') + delta)
    if (deltaRafRef.current === null) {
      deltaRafRef.current = requestAnimationFrame(() => {
        deltaRafRef.current = null
        flushDeltasNow()
      })
    }
  }, [flushDeltasNow, clearWaitLine])

  // 071: same per-frame batching for the reasoning trace. Buffer per message id
  // carries accumulated text + the latest wall-ms so the folded pill can label
  // "Thought Ns". A reasoning delta can arrive before the first answer token, so
  // the buffer key is the pending assistant bubble's id (set by onNewMessage).
  const reasoningBufRef = useRef<Map<string, { text: string; ms: number }>>(new Map())
  const reasoningRafRef = useRef<number | null>(null)
  const flushReasoningNow = useCallback(() => {
    if (reasoningRafRef.current !== null) {
      cancelAnimationFrame(reasoningRafRef.current)
      reasoningRafRef.current = null
    }
    const buf = reasoningBufRef.current
    if (buf.size === 0) return
    reasoningBufRef.current = new Map()
    mapAllConvMessages((m) =>
      m.some((msg) => buf.has(msg.id))
        ? m.map((msg) => {
            const add = buf.get(msg.id)
            return add
              ? { ...msg, reasoning: (msg.reasoning ?? '') + add.text, reasoning_ms: add.ms }
              : msg
          })
        : m,
    )
  }, [mapAllConvMessages])
  const queueReasoning = useCallback((id: string, text: string, ms?: number) => {
    const prev = reasoningBufRef.current.get(id)
    reasoningBufRef.current.set(id, {
      text: (prev?.text ?? '') + text,
      ms: ms ?? prev?.ms ?? 0,
    })
    if (reasoningRafRef.current === null) {
      reasoningRafRef.current = requestAnimationFrame(() => {
        reasoningRafRef.current = null
        flushReasoningNow()
      })
    }
  }, [flushReasoningNow])

  // 078: live tool-run frames (ui.live.*). open/close apply at once (they are
  // rare boundaries); append is coalesced per frame like deltas so a chatty
  // script cannot re-render the timeline per line.
  const liveBufRef = useRef<Map<string, LiveEvent[]>>(new Map())
  const liveRafRef = useRef<number | null>(null)
  const flushLiveNow = useCallback(() => {
    if (liveRafRef.current !== null) {
      cancelAnimationFrame(liveRafRef.current)
      liveRafRef.current = null
    }
    const buf = liveBufRef.current
    if (buf.size === 0) return
    liveBufRef.current = new Map()
    const now = Date.now()
    mapAllConvMessages((m) =>
      m.some((msg) => buf.has(msg.id))
        ? m.map((msg) => {
            const evts = buf.get(msg.id)
            if (!evts) return msg
            let runs = msg.live_runs
            for (const e of evts) runs = applyLiveEvent(runs, e, now)
            return runs === msg.live_runs ? msg : { ...msg, live_runs: runs }
          })
        : m,
    )
  }, [mapAllConvMessages])
  const queueLive = useCallback((id: string, evt: LiveEvent) => {
    const list = liveBufRef.current.get(id) ?? []
    list.push(evt)
    liveBufRef.current.set(id, list)
    if (evt.type === 'ui.live.append') {
      if (liveRafRef.current === null) {
        liveRafRef.current = requestAnimationFrame(() => {
          liveRafRef.current = null
          flushLiveNow()
        })
      }
    } else {
      flushLiveNow()
    }
  }, [flushLiveNow])
  /** Route a ui_event frame: live-run frames go to the pending bubble, the
   *  rest to the shell-level handler (scoped to the stream's conversation). */
  const routeUiEvent = useCallback((convId: string | null, msgId: string, evt: { type: string; [key: string]: any }) => {
    if (isLiveEvent(evt)) queueLive(msgId, evt as LiveEvent)
    else handleUiEvent(convId, evt)
  }, [queueLive, handleUiEvent])

  // 006.7: trigger a headless agent turn so the model can respond to a
  // rejection that landed after the original SSE stream closed.
  function triggerContinuation(convId: string) {
    if (streamingConvsRef.current.has(convId)) return
    setConvStreaming(convId, true)
    const asstId = `local-cont-${Date.now()}`
    setConvMessages(convId, (m) => [
      ...m,
      { id: asstId, role: 'assistant', content: '', pending: true, created_at: new Date().toISOString() },
    ])
    let currentMsgId = asstId
    // 036/phase05 fix: needs_continuation is only ACTED on after the stream
    // promise settles (the SSE is provably closed and streaming is cleared).
    // The old fixed 400ms timer inside onDone raced the server's post-done
    // cleanup — when closing the aborted provider stream took longer, the
    // streamingRef guard silently swallowed the follow-up (dojo M3 defect).
    let needsChain = false
    continueConversation(convId, {
      onDelta: (delta) => queueDelta(convId, currentMsgId, delta),
      onReasoning: (r) => queueReasoning(currentMsgId, r.text, r.ms),
      // 089: a continuation is the same logical turn — its tool calls keep
      // counting on the same chip (no reset here).
      onToolCall: (names) => logToolCalls(convId, names),
      onToolResult: (name, result) => logToolResult(convId, name, result),
      onNewMessage: (id) => {
        currentMsgId = id
        setConvMessages(convId, (m) => [
          ...m,
          { id, role: 'assistant', content: '', pending: true, created_at: new Date().toISOString() },
        ])
      },
      onUiEvent: (evt) => routeUiEvent(convId, currentMsgId, evt),
      onNotice: (n) => {
        setConvMessages(convId, (m) => [
          ...m,
          {
            id: `notice-${Date.now()}`,
            role: 'system',
            content: '',
            notice: { from: n.from, to: n.to, reason: n.reason },
            created_at: new Date().toISOString(),
          },
        ])
      },
      // 039: billing gateway refused the turn — action-required banner (the
      // server also persists a marker row, so reloads keep the explanation).
      onPolicyBlocked: (b) => {
        setConvMessages(convId, (m) => [
          ...m,
          {
            id: `policy-block-${Date.now()}`,
            role: 'system',
            content: '',
            policy_block: { code: b.code, message: b.message, retryable: b.retryable },
            created_at: new Date().toISOString(),
          },
        ])
      },
      // 080: honest failure card (storage down, model 4xx, …).
      onTurnError: (n) => {
        flushDeltasNow()
        flushReasoningNow()
        flushLiveNow()
        applyTurnError(convId, currentMsgId, n)
      },
      onDone: (_text, meta) => {
        flushDeltasNow()
        flushReasoningNow()
        flushLiveNow()
        // 036/phase02: a superseded draft is persisted server-side as
        // superseded_partial — keep it (dimmed/collapsed), never a silent void.
        setConvMessages(convId, (m) =>
          meta?.superseded
            ? m.flatMap((msg) =>
                msg.id !== currentMsgId
                  ? [msg]
                  : msg.content.trim()
                    ? [{ ...msg, pending: false, kind: 'superseded_partial' }]
                    : [])
            : m.map((msg) => (msg.id === currentMsgId ? { ...msg, pending: false } : msg)),
        )
        if (meta?.stopped) addSystemLine(convId, stopMarkerText(meta.stopped))
        if (meta?.context && activeIdRef.current === convId) setContextStatus(meta.context)
        // 031: messages sent DURING a follow-up turn queue again — chain another
        // continuation so every mid-turn message is ingested, WhatsApp-style.
        if (meta?.needs_continuation) needsChain = true
      },
      onError: () => {},
    })
      .catch(() => {})
      .finally(() => {
        flushDeltasNow()
        flushReasoningNow()
        flushLiveNow()
        setConvStreaming(convId, false)
        patchTurn(convId, { waitLine: null })
        settleToolLog(convId)
        // 012: the drain belongs to ITS conversation — chain even if the user
        // switched away (isolated state makes the background turn safe).
        if (needsChain) {
          setTimeout(() => triggerContinuation(convId), 400)
        }
      })
  }

  // 028: re-attach a viewer to a still-running server-owned turn (page reload
  // or tab return mid-turn). The DB rows just loaded contain every persisted
  // segment; the server replays only from the last segment boundary, so the
  // two sources never overlap. Without this a reloaded tab showed a dead chat
  // while the turn kept working invisibly — the RayLa incident.
  const attachCtrlRef = useRef<AbortController | null>(null)
  useEffect(() => () => { attachCtrlRef.current?.abort() }, [])
  async function reattachIfActive(convId: string) {
    if (streamingConvsRef.current.has(convId)) return
    let status: { active: boolean }
    try { status = await getTurnStatus(convId) } catch { return }
    if (!status.active || activeIdRef.current !== convId) return
    attachCtrlRef.current?.abort()
    const ctrl = new AbortController()
    attachCtrlRef.current = ctrl
    setConvStreaming(convId, true)
    const asstId = `local-att-${Date.now()}`
    setConvMessages(convId, (m) => [
      ...m,
      { id: asstId, role: 'assistant', content: '', pending: true, created_at: new Date().toISOString() },
    ])
    let currentMsgId = asstId
    let needsCont = false
    const resync = () => {
      api.messages(convId)
        .then((msgs) => setConvMessages(convId, msgs.map(apiToUI)))
        .catch(() => {})
    }
    try {
      await attachTurnStream(convId, {
        onDelta: (delta) => queueDelta(convId, currentMsgId, delta),
        onReasoning: (r) => queueReasoning(currentMsgId, r.text, r.ms),
        onToolCall: (names) => {
          patchTurn(convId, (t) => ({ toolNames: [...t.toolNames, ...names] }))
          logToolCalls(convId, names)
        },
        onToolResult: (name, result) => logToolResult(convId, name, result),
        onNewMessage: (id) => {
          currentMsgId = id
          setConvMessages(convId, (m) => [
            ...m,
            { id, role: 'assistant', content: '', pending: true, created_at: new Date().toISOString() },
          ])
        },
        onUiEvent: (evt) => routeUiEvent(convId, currentMsgId, evt),
        onCondenseStarted: () => patchTurn(convId, { condensing: true }),
        onCondenseDone: (r) => {
          patchTurn(convId, { condensing: false })
          if (r.context && activeIdRef.current === convId) setContextStatus(r.context)
        },
        onDone: (_text, meta) => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          // 036/phase02: the superseded draft IS in the DB (superseded_partial)
          // — drop only the local pending bubble; resync renders the truth,
          // including the ⏹ turn_stopped marker row.
          setConvMessages(convId, (m) =>
            meta?.superseded
              ? m.filter((msg) => !msg.pending)
              : m.map((msg) => (msg.pending ? { ...msg, pending: false } : msg)),
          )
          if (meta?.context && activeIdRef.current === convId) setContextStatus(meta.context)
          // 036/phase05: a watched turn that breaks still owes its follow-up —
          // this tab may be the only client left to fire it.
          if (meta?.needs_continuation) needsCont = true
          resync()
        },
        onClosed: () => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          setConvMessages(convId, (m) => m.map((msg) => (msg.pending ? { ...msg, pending: false } : msg)))
          resync()
        },
        onError: () => {},
      }, ctrl.signal)
    } catch {
      // 404 (turn finished between status check and attach) or a dropped
      // stream — the DB has the final state either way.
      resync()
    } finally {
      flushDeltasNow()
      flushReasoningNow()
      if (attachCtrlRef.current === ctrl) {
        attachCtrlRef.current = null
      }
      setConvStreaming(convId, false)
      patchTurn(convId, { toolNames: [], waitLine: null, condensing: false })
      settleToolLog(convId)
      if (needsCont) {
        setTimeout(() => triggerContinuation(convId), 400)
      }
    }
  }

  // 089: approval ids auto-approved DURING a live turn we're painting. Their
  // timeline receipts are suppressed — the wrench progress chip is the one
  // in-turn tool surface (the old receipts made the timeline grow every call).
  // Rehydrated history (older turns, reloads) is NOT tagged and keeps its
  // coalesced AutoToolReceipts rows.
  const liveAutoIdsRef = useRef<Set<string>>(new Set())

  // Subscribe once to the global approval event stream. We keep every record
  // for the session and filter by conversation at render time, so switching
  // conversations shows the right cards without re-subscribing.
  useEffect(() => {
    const ctrl = new AbortController()
    subscribeApprovalEvents(
      {
        onReady: () => setOffline(false),
        onError: () => setOffline(true),
        onRequested: (r) =>
          setApprovals((prev) =>
            prev.some((a) => a.req.id === r.id) ? prev : [...prev, { req: r, decided: null }],
          ),
        onDecided: (info) => {
          const id = (info as { request_id?: string; id?: string }).request_id ?? (info as { id?: string }).id
          // 089: an auto-approval landing while its conversation streams is
          // in-turn tool activity — the chip covers it, the timeline doesn't.
          const decidedConv = (info as { conversation_id?: string }).conversation_id
          if (id && (info as { auto?: boolean }).auto === true
              && decidedConv && streamingConvsRef.current.has(decidedConv)) {
            liveAutoIdsRef.current.add(id)
          }
          setApprovals((prev) =>
            prev.map((a) =>
              a.req.id === id
                ? {
                    ...a,
                    decided: {
                      decision: info.decision,
                      reason: (info as { reason?: string }).reason,
                      // 010.5: carry how it resolved so the timeline can render
                      // a silent auto-approval as a tiny line, not a green block.
                      decidedBy: (info as { decided_by?: string }).decided_by ?? null,
                      auto: (info as { auto?: boolean }).auto === true,
                    },
                  }
                : a,
            ),
          )
          // 006.7: auto-continue after rejection so the agent acknowledges it.
          // ONLY for the conversation currently on screen — a grouped
          // rejection can include approvals from older conversations, and
          // continuing those would stream a foreign agent turn into this view.
          if (info.decision === 'rejected') {
            const convId = (info as { conversation_id?: string }).conversation_id ?? activeIdRef.current
            if (convId && convId === activeIdRef.current && !streamingConvsRef.current.has(convId)) {
              setTimeout(() => {
                if (activeIdRef.current === convId) triggerContinuation(convId)
              }, 300)
            }
          }
        },
        onExpired: (info) =>
          setApprovals((prev) =>
            prev.map((a) =>
              a.req.id === info.request_id ? { ...a, decided: { decision: 'expired' } } : a,
            ),
          ),
        // 049/phase04: verifier (or other advisory) annotation lands on a
        // pending card after it was rendered — patch the request in place.
        onAnnotated: (info) =>
          setApprovals((prev) =>
            prev.map((a) =>
              a.req.id === info.id
                ? {
                    ...a,
                    req: {
                      ...a.req,
                      annotations: { ...(a.req.annotations ?? {}), [info.key]: info.value },
                    },
                  }
                : a,
            ),
          ),
        // 006.708: vault secret-form lifecycle.
        onSecretRequested: (info) =>
          setSecretReqs((prev) =>
            prev.some((r) => r.id === info.request_id)
              ? prev
              : [
                  ...prev,
                  {
                    id: info.request_id,
                    name: info.name,
                    kind: info.kind,
                    reason: info.reason ?? null,
                    conversation_id: info.conversation_id ?? null,
                    status: 'pending',
                    created_at: new Date().toISOString(),
                    resolved_at: null,
                  },
                ],
          ),
        // 027: plan snapshots that land BETWEEN turns (turn-end demotion,
        // dismiss/resume from another tab). The per-turn chat SSE is closed by
        // then, so the paused/updated card arrives over this global stream.
        onUiTasks: (info) => {
          setPlanTasks((info.tasks as PlanTask[]) ?? [])
          setPlanCreatedAt(info.created_at ?? null)
          setPlanConversationId(info.conversation_id ?? null)
          setPlanTurnActive(!!info.turn_active)
        },
        // 006.712: live append of messages posted outside the chat stream
        // (send_chat_message from background runs). Only for the conversation
        // on screen; dedupe by message id (it could arrive via refetch too).
        onMessageCreated: (info) => {
          // 026: a plan anchor means the live plan is finished — clear the
          // sticky card in EVERY conversation (ui.tasks only reaches the
          // streaming tab; message.* reaches them all).
          if (info.kind === 'task_plan') {
            setPlanTasks([])
            setPlanCreatedAt(null)
            setPlanConversationId(null)
          }
          // 012: route to the message's OWN conversation — never the one on
          // screen. Events without a conversation id are dropped (they used to
          // land in whatever chat was open).
          const msgConvId = info.conversation_id
          if (!msgConvId) return
          // 008.994: a muted line arrives as role="user" + kind="muted"; the
          // agent's reply arrives as role="assistant". Honor the role/kind from
          // the event instead of assuming assistant.
          const role = (info.role === 'user' ? 'user' : 'assistant') as UIMessage['role']
          setConvMessages(msgConvId, (m0) => {
            // 076: an assistant reply landing means the server drained the
            // queue — every earlier "Sending…" user bubble has been handled.
            const m =
              role === 'assistant' && m0.some((x) => x.queued)
                ? m0.map((x) => (x.queued ? { ...x, queued: false } : x))
                : m0
            if (m.some((msg) => msg.id === info.message_id)) return m
            const msg: UIMessage = {
              id: info.message_id,
              role,
              content: info.content,
              created_at: info.created_at || new Date().toISOString(),
              source: info.source || null,
              kind: info.kind || undefined,
              // 056: card rows carry their embed in the event so the
              // live append renders without a refetch.
              embed_iframe: info.embed_iframe || undefined,
              title: info.title || undefined,
              plan_status: info.plan_status || undefined,
              tasks: info.tasks || undefined,
            }
            // 056: the global SSE can deliver a mid-turn card AFTER the turn's
            // own rows were committed — when the event carries the row's server
            // timestamp, insert in timeline order instead of appending.
            const ts = info.created_at ? Date.parse(info.created_at) : NaN
            if (Number.isFinite(ts)) {
              let at = m.length
              while (at > 0 && Date.parse(m[at - 1].created_at || '') > ts) at--
              return [...m.slice(0, at), msg, ...m.slice(at)]
            }
            return [...m, msg]
          })
        },
        onSecretResolved: (info) => {
          setSecretReqs((prev) =>
            prev.map((r) =>
              r.id === info.request_id
                ? { ...r, status: info.status, resolved_at: new Date().toISOString() }
                : r,
            ),
          )
          // The requesting turn already ended (request_credential returns
          // immediately) — continue so the agent acknowledges the outcome.
          const convId = info.conversation_id ?? activeIdRef.current
          if (convId && convId === activeIdRef.current && !streamingConvsRef.current.has(convId)) {
            setTimeout(() => {
              if (activeIdRef.current === convId) triggerContinuation(convId)
            }, 300)
          }
        },
        // 008.9: a stop was issued for this conversation (button, /stop, or any
        // other surface). Put the originating message back in the composer; for
        // a hard stop, clear streaming immediately (the stream is being killed).
        // 012: scoped to the stopped conversation.
        onChatStopped: (info) => {
          const convId = info.conversation_id
          if (!convId) return
          if (convId === activeIdRef.current && lastUserTextRef.current) {
            setInput((cur) => cur || lastUserTextRef.current)
          }
          if (info.stage === 'hard') {
            setConvStreaming(convId, false)
            patchTurn(convId, { stopStage: 0, waitLine: null })
            // 0.21.002: hard stop kills the stream, so onDone may never fire to
            // clear `pending`. Stop the "thinking" dots on any still-pending
            // assistant bubble that has no content yet.
            setConvMessages(convId, (m) =>
              m.map((msg) =>
                msg.role === 'assistant' && msg.pending && !msg.content
                  ? { ...msg, pending: false }
                  : msg,
              ),
            )
          }
        },
      },
      ctrl.signal,
    )
    return () => ctrl.abort()
  }, [])

  // 089: live conversation-state sync. Another surface (the agent, another
  // tab, the server's ops loop) changed a conversation's state — patch the
  // row in place so the sidebar and the composer pulldown follow. The state
  // is NEVER flipped programmatically from here beyond mirroring the server.
  useEffect(() => {
    const stop = subscribeConvStateEvents((ev) => {
      setConversations((prev) =>
        sortOpsFirst(
          prev.map((c) =>
            c.id === ev.conversation_id
              ? { ...c, ...(ev.kind ? { kind: ev.kind } : {}), state: ev.state ?? c.state }
              : c,
          ),
        ),
      )
    })
    return stop
  }, [])

  // 005.913 — hidden Cmd+D / Ctrl+D debug mode. State is per-tab; activating
  // it backfills the per-conversation ring buffer and opens a parallel SSE
  // (topics=*) so live tool calls / bus events / approvals flow into the
  // chat as inline debug rows. When off, we keep the buffer (cheap) so a
  // toggle-back is instant, but we close the SSE so there's zero overhead.
  const [debugMode] = useDebugMode()
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([])
  // Active id at time of debug subscription. We re-subscribe on conversation
  // change so events are scoped correctly (we filter client-side too, but
  // filtering at subscribe time keeps state small).
  useEffect(() => {
    if (!debugMode || !activeId) return
    const ctrl = new AbortController()
    let cancelled = false
    void fetchDebugEventsBackfill(activeId, getToken()).then((evs) => {
      if (cancelled) return
      setDebugEvents(evs)
    })
    subscribeAllEvents(
      {
        onEvent: (e) => {
          // Only keep events tied to the current conversation. The server
          // already drops global events, but a conversation_id field from a
          // *different* conversation can sneak through if the server has
          // multiple chats open. Filter defensively.
          const cid =
            (e.payload as { conversation_id?: string })?.conversation_id ??
            null
          if (cid && cid !== activeId) return
          setDebugEvents((prev) => [...prev, e])
        },
      },
      ctrl.signal,
    )
    return () => {
      cancelled = true
      ctrl.abort()
    }
  }, [debugMode, activeId])
  // Drop the in-memory debug events when switching conversations so stale
  // events from the previous chat don't bleed into the next.
  useEffect(() => {
    setDebugEvents([])
  }, [activeId])

  const refreshConversations = useCallback(async () => {
    try {
      const c = await api.conversations()
      setConversations(sortOpsFirst(c as ConvRow[]))
      return c
    } catch {
      return []
    } finally {
      setLoadingConvs(false)
    }
  }, [])

  // 089: the composer's state pulldown changed — the ONLY place this client
  // writes a conversation state (user interaction; never programmatic).
  // Optimistic row patch, PATCH to the server, re-sync from it on failure.
  const changeConvState = useCallback((convId: string, state: string) => {
    setConversations((prev) =>
      prev.map((c) => (c.id === convId ? { ...c, state } : c)),
    )
    void patchConversationState(convId, state).catch(() => {
      void refreshConversations()
    })
  }, [refreshConversations])

  // 028: the warm-boot cache must mirror EVERY list mutation (delete, create,
  // rename), not just refreshes — otherwise a deleted conversation repaints
  // from the stale cache on the next reload and looks resurrected.
  useEffect(() => {
    if (!loadingConvs) cachedSet('conversations', conversations)
  }, [conversations, loadingConvs])

  // After an onboarding turn, refresh identity (name/emoji/persona may have
  // just been recorded) and detect completion so the banner clears.
  const syncOnboarding = useCallback(async () => {
    if (!needsOnboarding) return
    try {
      const id = await api.identity()
      onIdentityChange?.(id)
      const st = await api.authStatus()
      if (st.onboarding_complete) onOnboardingComplete?.()
    } catch {
      /* ignore */
    }
  }, [needsOnboarding, onIdentityChange, onOnboardingComplete])

  // First run: let Luna open the conversation herself.
  const kickoff = useCallback(async () => {
    // 012: onboarding streams before any conversation exists — it lives under
    // the '' key until the server-created conversation is selected below.
    setConvStreaming(null, true)
    patchTurn(null, { toolNames: [], subagent: null })
    const assistantMsg: UIMessage = { id: `local-a-${Date.now()}`, role: 'assistant', content: '', pending: true }
    setConvMessages(null, [assistantMsg])
    try {
      await startOnboardingStream({
        onDelta: (delta) => queueDelta(null, assistantMsg.id, delta),
        onToolCall: (names) => patchTurn(null, (t) => ({ toolNames: [...t.toolNames, ...names] })),
        onDone: () => {
          flushDeltasNow()
          setConvMessages(null, (m) => m.map((x) => (x.id === assistantMsg.id ? { ...x, pending: false } : x)))
        },
      })
    } finally {
      flushDeltasNow()
      setConvStreaming(null, false)
      patchTurn(null, { toolNames: [] })
      const c = await refreshConversations()
      // The onboarding turn persisted its rows server-side — select the
      // conversation properly so its timeline loads under its own key.
      if (c.length > 0) void selectConversation(c[0].id)
      syncOnboarding()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshConversations, syncOnboarding])

  useEffect(() => {
    // 007.013-D: trust the URL's conversation id unconditionally. The list
    // fetch can be momentarily stale during an active stream (refresh while
    // the agent is "thinking"), which previously made us fall back to c[0]
    // and abandon the user's conversation — their just-sent message would
    // "vanish" even though it's safely persisted. Select the URL conv
    // directly; only fall back if it genuinely doesn't exist (404).
    // 045/phase05: fire the URL conversation's message fetch IN PARALLEL with
    // the conversations list — serializing them added a full round trip to
    // first paint for every deep link / reload.
    const urlSelect = initialConversationId
      ? selectConversation(initialConversationId)
      : Promise.resolve(false)
    refreshConversations().then(async (c) => {
      if (initialConversationId && (await urlSelect)) return
      // Phase 15: onboarding kickoff fires whenever setup is incomplete AND
      // the latest conversation is empty — not only when zero conversations
      // exist. A brand-new agent that happens to already have an empty
      // conversation still gets the proactive greeting.
      if (needsOnboarding && !kickedOff.current) {
        let latestEmpty = c.length === 0
        if (c.length > 0) {
          try {
            const msgs = await api.messages(c[0].id)
            latestEmpty = msgs.length === 0
          } catch {
            latestEmpty = false
          }
        }
        if (latestEmpty) {
          kickedOff.current = true
          kickoff()
          return
        }
      }
      // 057 phase04: on mobile the conversation list is the root screen —
      // don't auto-push the last conversation (it would hide the bottom nav
      // on every app open). Desktop keeps the auto-select.
      if (c.length > 0 && !isMobileViewport()) {
        selectConversation(c[0].id)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshConversations])

  useEffect(() => {
    if (!loadingMessages && isAtBottomRef.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    } else if (!loadingMessages && !isAtBottomRef.current) {
      setShowNewMessages(true)
    }
  }, [messages, approvals, loadingMessages])

  // Card iframes auto-size AFTER the messages effect above has scrolled
  // (assets inline → iframe mounts → luna:embed:height grows it), so a fresh
  // card used to push the timeline bottom out of view. Follow the growth as
  // long as the user is at the bottom; rAF runs after the height applies.
  useEffect(() => {
    const onEmbedHeight = (e: MessageEvent) => {
      const d = e.data as { type?: string } | null
      if (!d || d.type !== 'luna:embed:height' || !isAtBottomRef.current) return
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'instant' })
      })
    }
    window.addEventListener('message', onEmbedHeight)
    return () => window.removeEventListener('message', onEmbedHeight)
  }, [])

  // Returns true if the conversation loaded, false if it could not be fetched
  // (e.g. 404) so callers can fall back. 007.013-D.
  async function selectConversation(id: string): Promise<boolean> {
    setActiveId(id)
    activeIdRef.current = id
    setLoadingMessages(true)
    try { setInput(localStorage.getItem(draftKey(id)) ?? '') } catch { setInput('') }
    // 008.95: staged uploads are bound to the conversation that minted their
    // refs — they don't survive a switch.
    clearStaged()
    // 012: NO turn-state reset here — every conversation carries its own
    // turn record, so switching shows the target chat's real state.
    let msgs
    try {
      msgs = await api.messages(id)
    } catch {
      setLoadingMessages(false)
      return false
    }
    // 045/phase05: apply-and-clear any buffered deltas before the overwrite so
    // a stale chunk can't misroute later.
    flushDeltasNow()
    // 045/phase05 (dojo catch): a message sent while this fetch was in flight
    // must survive the overwrite — the snapshot predates it, so blindly
    // replacing state here wiped the user's just-sent bubble (and the live
    // assistant bubble streaming into it). Keep trailing local-* messages;
    // 012: the merge now only ever sees THIS conversation's in-flight bubbles.
    setConvMessages(id, (prev) => {
      const locals = prev.filter((x) => x.id.startsWith('local-'))
      const loaded = msgs.map(apiToUI)
      return locals.length ? [...loaded, ...locals] : loaded
    })
    api.context(id).then(setContextStatus).catch(() => setContextStatus(null))
    await Promise.all([loadConversationApprovals(id), loadConversationSecretReqs(id)])
    setLoadingMessages(false)
    // Jump to bottom instantly (no visible scroll animation on load)
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'instant' })
    })
    // 028: a server-owned turn may still be running for this conversation
    // (reload mid-turn) — re-attach as a viewer instead of showing a dead chat.
    void reattachIfActive(id)
    return true
  }

  // 005.82-fixes1: re-hydrate a conversation's approval cards on (re)load.
  // The live SSE stream only delivers events for the current session, so a
  // refresh would otherwise drop every previously-decided card. We fetch all
  // approvals for the conversation and merge by id — pending ones become
  // actionable cards again, decided ones render as the resolved chip.
  async function loadConversationApprovals(id: string) {
    try {
      const rows = await api.approvals.list({ conversation_id: id, status: 'all', limit: 200 })
      setApprovals((prev) => {
        const byId = new Map(prev.map((a) => [a.req.id, a]))
        for (const req of rows) {
          const decided =
            req.status === 'pending'
              ? null
              : {
                  decision: req.status,
                  reason: req.decision_reason ?? null,
                  // 010.5: rehydrate the auto/owner distinction from history.
                  decidedBy: req.decided_by ?? null,
                  auto: (req.decided_by ?? '').startsWith('auto:'),
                }
          byId.set(req.id, { req, decided })
        }
        return Array.from(byId.values())
      })
    } catch {
      /* history unavailable (e.g. in-memory engine) — ignore */
    }
  }

  // 006.708: re-hydrate secret-form cards on conversation (re)load — same
  // reasoning as approvals: SSE has no replay, refresh must not drop forms.
  async function loadConversationSecretReqs(id: string) {
    try {
      const rows = await api.secretRequests.list(id)
      setSecretReqs((prev) => {
        const byId = new Map(prev.map((r) => [r.id, r]))
        for (const r of rows) byId.set(r.id, r)
        return Array.from(byId.values())
      })
    } catch {
      /* vault routes unavailable — ignore */
    }
  }

  // 005.904: the live approval.requested SSE event can be missed (the EventBus
  // has no replay, and the SSE connection can be mid-reconnect/starved) — that's
  // the "card only shows after refresh" bug. A gated turn stays `streaming` while
  // it's blocked awaiting the owner, so while streaming we poll the
  // conversation's approvals as a self-heal backstop.
  // 006.708: secret forms ride the same poll.
  // SPEED101 (plan 027 §12): the live path is the /api/events SSE stream
  // (approval.requested / vault.secret_requested are handled there) — the poll
  // is only the missed-event fallback, so 10 s is plenty and cuts 2 requests
  // every 2 s per streaming client through the proxy.
  useEffect(() => {
    if (!streaming || !activeId) return
    const iv = setInterval(() => {
      void loadConversationApprovals(activeId)
      void loadConversationSecretReqs(activeId)
    }, 10000)
    return () => clearInterval(iv)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming, activeId])

  async function newConversation() {
    const c = await api.createConversation()
    setConversations((cs) => sortOpsFirst([c as ConvRow, ...cs]))
    setActiveId(c.id)
    activeIdRef.current = c.id
    // A brand-new conversation has no saved draft, so only *restore* one if it
    // somehow exists — never clobber with '' here. createConversation() is
    // async, so a blind setInput('') after the await would wipe text the user
    // (or a test) typed immediately after clicking "New chat" (item B race).
    try {
      const d = localStorage.getItem(draftKey(c.id))
      if (d) setInput(d)
    } catch { /* localStorage unavailable */ }
    setConvMessages(c.id, [])
  }

  // 008.9: stop the active turn. First click → soft (finish current step);
  // a second click, or the already-soft state, escalates to a hard stop.
  function handleStop() {
    const convId = activeIdRef.current
    if (!convId) return
    const hard = stopStage >= 1
    void stopTurn(convId, hard).catch(() => {})
    if (hard) {
      patchTurn(convId, { stopStage: 2 })
      addSystemLine(convId, 'Hard-stopping the agent.')
    } else {
      patchTurn(convId, { stopStage: 1 })
      addSystemLine(convId, 'Stopping agent at the end of this step…')
    }
  }

  // 008.95 — composer attachment staging.
  async function uploadStagedItem(item: StagedAttachment, convId: string): Promise<AttachmentInfo | null> {
    setStaged((s) => s.map((x) => (x.id === item.id ? { ...x, status: 'uploading' } : x)))
    try {
      const info = await uploadAttachment(convId, item.file)
      setStaged((s) => s.map((x) => (x.id === item.id ? { ...x, status: 'ready', info } : x)))
      return info
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setStaged((s) => s.map((x) => (x.id === item.id ? { ...x, status: 'error', error: msg } : x)))
      return null
    }
  }

  function stageFiles(files: File[]) {
    if (!files.length) return
    const items: StagedAttachment[] = files.map((f) => ({
      id: `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file: f,
      status: 'staged',
      previewUrl: f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined,
    }))
    setStaged((s) => [...s, ...items])
    const convId = activeIdRef.current
    if (convId) {
      for (const it of items) uploadPromisesRef.current.set(it.id, uploadStagedItem(it, convId))
    }
  }

  function removeStaged(id: string) {
    uploadPromisesRef.current.delete(id)
    setStaged((s) => {
      const it = s.find((x) => x.id === id)
      if (it?.previewUrl) URL.revokeObjectURL(it.previewUrl)
      return s.filter((x) => x.id !== id)
    })
  }

  /** Finish (or start) every staged upload against convId. Returns what the
   * server should bind (`send`) and what the optimistic bubble should show
   * (`display` — local object-URL previews for images). Failed uploads are
   * dropped: their chip showed the error before send. */
  async function resolveStagedUploads(
    convId: string,
    items: StagedAttachment[],
  ): Promise<{ send: AttachmentInfo[]; display: AttachmentInfo[] }> {
    const resolved = await Promise.all(
      items.map((it) => {
        if (it.status === 'ready' && it.info) return Promise.resolve<AttachmentInfo | null>(it.info)
        const pending = uploadPromisesRef.current.get(it.id)
        if (pending) return pending
        const p = uploadStagedItem(it, convId)
        uploadPromisesRef.current.set(it.id, p)
        return p
      }),
    )
    const send: AttachmentInfo[] = []
    const display: AttachmentInfo[] = []
    items.forEach((it, i) => {
      const info = resolved[i]
      if (!info) return
      send.push(info)
      display.push({ ...info, url: it.previewUrl ?? info.url })
    })
    return { send, display }
  }

  function clearStaged() {
    uploadPromisesRef.current.clear()
    setStaged([])
  }

  sendRef.current = send

  // 065: textOverride comes from the chat bridge ('send' action) — the text
  // is not the composer draft, so the draft must survive untouched.
  async function send(textOverride?: string) {
    const fromBridge = typeof textOverride === 'string'
    const text = (fromBridge ? textOverride : input).trim()
    const stagedItems = fromBridge ? [] : stagedRef.current.filter((x) => x.status !== 'error')
    if (!text && !stagedItems.length) return

    // 011: regex frustration check on the user's OWN typed text (bridge sends
    // come from plugin cards — skip). Fires at most once per conversation per
    // cooldown window, and only when plugin-feedback is installed.
    if (!fromBridge && hasFeedback && text && !text.startsWith('/')) {
      const fbConv = activeIdRef.current
      if (fbConv && !alertSuppressed(fbConv) && matchesFrustration(text)) {
        setFeedbackAlertConv(fbConv)
      }
    }

    // 073/phase002: /condense — summarize the conversation's older head now.
    // A command, not a message: nothing is sent to the agent.
    if (text === '/condense') {
      if (!fromBridge) setInput('')
      const condConvId = activeIdRef.current
      if (!condConvId) {
        addSystemLine(null, 'Nothing to condense — no conversation yet.')
        return
      }
      if (streaming || condensing) {
        addSystemLine(condConvId, 'Busy — wait for the current turn to finish, then retry /condense.')
        return
      }
      patchTurn(condConvId, { condensing: true })
      try {
        const r = await api.condense(condConvId)
        if (r.context && activeIdRef.current === condConvId) setContextStatus(r.context)
        addSystemLine(condConvId, r.condensed
          ? 'Conversation condensed — older messages folded into a summary.'
          : 'Nothing to condense yet.')
      } catch (e) {
        addSystemLine(condConvId, `Condense failed: ${(e as Error).message}`)
      } finally {
        patchTurn(condConvId, { condensing: false })
      }
      return
    }

    // 031: while a turn is running, sending is WhatsApp-style — the message
    // shows as a normal bubble immediately and is queued server-side. The
    // running turn breaks at the next node boundary and a follow-up turn ingests
    // it, so the agent genuinely responds. "/stop" still maps to the Stop button.
    if (streaming) {
      const convId = activeIdRef.current
      if (!convId) return
      if (text === '/stop' || text === '/stop!') {
        if (!fromBridge) setInput('')
        const wantHard = text === '/stop!' || stopStage >= 1
        void stopTurn(convId, wantHard).catch(() => {})
        addSystemLine(convId, wantHard ? 'Hard-stopping the agent.' : 'Stopping agent at the end of this step…')
        patchTurn(convId, { stopStage: wantHard ? 2 : 1 })
        return
      }
      if (!fromBridge) {
        setInput('')
        try { localStorage.removeItem(draftKey(activeIdRef.current)) } catch { /* ignore */ }
      }
      // 008.95: finish any staged uploads, then bind them to the queued message.
      let qAtts: AttachmentInfo[] = []
      let qDisplay: AttachmentInfo[] = []
      if (stagedItems.length) {
        const r = await resolveStagedUploads(convId, stagedItems)
        qAtts = r.send
        qDisplay = r.display
        clearStaged()
        if (!text && !qAtts.length) return
      }
      const qId = `local-uq-${Date.now()}`
      const qMsg: UIMessage = {
        id: qId,
        role: 'user',
        content: text,
        created_at: new Date().toISOString(),
        queued: true,
        attachments: qDisplay.length ? qDisplay : undefined,
      }
      setConvMessages(convId, (m) => [...m, qMsg])
      isAtBottomRef.current = true
      setShowNewMessages(false)
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
      })
      queueMessage(convId, text, false, qAtts)
        .then((ack) =>
          setConvMessages(convId, (m) =>
            m.map((x) => (x.id === qId ? { ...x, id: ack.id || x.id, queued: false } : x)),
          ),
        )
        .catch(() => {
          // Keep the bubble; it's persisted server-side and reconciles on reload.
          setConvMessages(convId, (m) => m.map((x) => (x.id === qId ? { ...x, queued: false } : x)))
        })
      return
    }

    // Duplicate-send guard: a turn is already being set up but `streaming`
    // hasn't rendered yet. The re-send is almost certainly the same message
    // (the composer only clears after setup), so drop it. 012: per chat.
    const busyKey = activeId ?? ''
    if (sendBusyConvsRef.current.has(busyKey)) return
    sendBusyConvsRef.current.add(busyKey)

    lastUserTextRef.current = text
    patchTurn(activeId, { stopStage: 0 })
    const draftId = activeId
    let convId = activeId
    if (!convId) {
      try {
        const c = await api.createConversation()
        setConversations((cs) => sortOpsFirst([c as ConvRow, ...cs]))
        convId = c.id
        setActiveId(c.id)
      } catch (e) {
        sendBusyConvsRef.current.delete(busyKey)
        throw e
      }
    }

    if (!fromBridge) {
      setInput('')
      try { localStorage.removeItem(draftKey(draftId)) } catch { /* ignore */ }
    }

    // 008.95: finish staged uploads (starting them now if the conversation
    // was only just created) before opening the turn.
    let sendAtts: AttachmentInfo[] = []
    let displayAtts: AttachmentInfo[] = []
    if (stagedItems.length) {
      const r = await resolveStagedUploads(convId, stagedItems)
      sendAtts = r.send
      displayAtts = r.display
      clearStaged()
      if (!text && !sendAtts.length) {
        // Every upload failed and there's no text — nothing to send.
        sendBusyConvsRef.current.delete(busyKey)
        return
      }
    }

    setConvStreaming(convId, true)
    patchTurn(convId, { toolNames: [], subagent: null })
    // 089: a NEW user turn starts a fresh tool count; the previous turn's
    // collapsed "🔧 n tools" receipt lived until exactly this moment.
    resetToolLog(convId)
    let needsContinuation = false
    const now = new Date().toISOString()
    const userMsg: UIMessage = {
      id: `local-u-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: now,
      attachments: displayAtts.length ? displayAtts : undefined,
    }
    const assistantMsg: UIMessage = {
      id: `local-a-${Date.now()}`,
      role: 'assistant',
      content: '',
      pending: true,
      created_at: new Date(Date.now() + 1).toISOString(),
    }
    setConvMessages(convId, (m) => [...m, userMsg, assistantMsg])
    // User just sent — scroll to bottom and resume auto-scroll
    isAtBottomRef.current = true
    setShowNewMessages(false)
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    })

    try {
      let currentMsgId = assistantMsg.id
      await sendMessageStream(convId, text, {
        onDelta: (delta) => queueDelta(convId, currentMsgId, delta),
        onReasoning: (r) => queueReasoning(currentMsgId, r.text, r.ms),
        onToolCall: (names) => {
          patchTurn(convId, (t) => ({ toolNames: [...t.toolNames, ...names] }))
          logToolCalls(convId, names)
        },
        onToolResult: (name, result, embed) => {
          logToolResult(convId, name, result)
          if (embed?.embed_iframe || embed?.embed_html) {
            const targetId = currentMsgId
            setConvMessages(convId, (m) => {
              const copy = [...m]
              const idx = copy.findIndex((x) => x.id === targetId)
              if (idx >= 0) {
                copy[idx] = {
                  ...copy[idx],
                  embed_iframe: embed.embed_iframe || copy[idx].embed_iframe,
                  embed_html: embed.embed_html || copy[idx].embed_html,
                }
              }
              return copy
            })
          }
        },
        onUiEvent: (evt) => routeUiEvent(convId, currentMsgId, evt),
        // 073/phase003: server-side pre-turn condense — block the composer
        // behind the condensing shimmer until the pass finishes.
        onCondenseStarted: () => patchTurn(convId, { condensing: true }),
        onCondenseDone: (r) => {
          patchTurn(convId, { condensing: false })
          if (r.context && activeIdRef.current === convId) setContextStatus(r.context)
        },
        onNotice: (n) => {
          setConvMessages(convId, (m) => [
            ...m,
            {
              id: `notice-${Date.now()}`,
              role: 'system',
              content: '',
              notice: { from: n.from, to: n.to, reason: n.reason },
              created_at: new Date().toISOString(),
            },
          ])
        },
        // 039: billing gateway refused the turn — action-required banner.
        onPolicyBlocked: (b) => {
          setConvMessages(convId, (m) => [
            ...m,
            {
              id: `policy-block-${Date.now()}`,
              role: 'system',
              content: '',
              policy_block: { code: b.code, message: b.message, retryable: b.retryable },
              created_at: new Date().toISOString(),
            },
          ])
        },
        onNewMessage: (id) => {
          const newMsg: UIMessage = {
            id,
            role: 'assistant',
            content: '',
            pending: true,
            created_at: new Date().toISOString(),
          }
          currentMsgId = id
          setConvMessages(convId, (m) => [...m, newMsg])
        },
        // 080: the turn failed with a typed notice — render the error card
        // (with Retry when retryable) instead of raw prose or "Stream failed".
        onTurnError: (n) => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          applyTurnError(convId, currentMsgId, n)
        },
        onDone: (_text, meta) => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          // 036/phase02: a superseded draft stays visible as a dimmed,
          // collapsed "interrupted" bubble (persisted server-side as
          // superseded_partial); the follow-up turn still owns THE reply.
          setConvMessages(convId, (m) =>
            meta?.superseded
              ? m.flatMap((msg) =>
                  msg.id !== currentMsgId
                    ? [msg]
                    : msg.content.trim()
                      ? [{ ...msg, pending: false, kind: 'superseded_partial' }]
                      : [])
              : m.map((msg) => (msg.id === currentMsgId ? { ...msg, pending: false } : msg)),
          )
          if (meta?.stopped) addSystemLine(convId, stopMarkerText(meta.stopped))
          if (meta?.context && activeIdRef.current === convId) setContextStatus(meta.context)
          // 008.9: a USER-stopped turn returns its originating message to the
          // composer. 034/phase04: 'injected' stops are internal hand-offs to a
          // follow-up turn — never refill the composer for those. 012: only if
          // this chat is still the one on screen.
          if (meta?.stopped && meta.stopped !== 'injected' && lastUserTextRef.current
              && activeIdRef.current === convId) {
            setInput((cur) => cur || lastUserTextRef.current)
          }
          // 007.004.1 / 008.9 / 031: auto-continue when a skill unlocked tools OR
          // when mid-turn messages are pending (the follow-up turn rebuilds
          // history from the DB and ingests the now-persisted user rows).
          // 036/phase05 fix: only record the intent here — the trigger fires in
          // the finally, once the stream has provably closed (see the note in
          // triggerContinuation; a fixed timer raced slow post-done cleanup).
          if (meta?.needs_continuation) needsContinuation = true
        },
        onError: (err) => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          setConvMessages(convId, (m) =>
            m.map((msg) =>
              msg.id === currentMsgId
                ? { ...msg, pending: false, content: msg.content || `Error: ${err.message}` }
                : msg,
            ),
          )
        },
        // 031: the server already had a turn running for this conversation
        // (another tab, a reload, or a silently-dead stream), so it queued our
        // message instead of streaming. Drop only the premature "thinking"
        // assistant bubble; KEEP the user message as a normal (queued) bubble.
        // The running turn breaks and a follow-up ingests it.
        onQueued: (ack) => {
          setConvMessages(convId, (m) =>
            m
              .filter((x) => x.id !== assistantMsg.id)
              .map((x) => (x.id === userMsg.id ? { ...x, id: ack?.id || x.id, queued: true } : x)),
          )
        },
        // Bugfix (stuck "thinking"): the stream closed with no terminal event
        // (proxy cap, dropped connection, server reaped a dead turn). Stop the
        // thinking dots and re-sync from the server so we show whatever output
        // persisted, instead of a bubble that spins forever.
        onClosed: () => {
          flushDeltasNow()
          flushReasoningNow()
          flushLiveNow()
          setConvMessages(convId, (m) => m.map((msg) => (msg.pending ? { ...msg, pending: false } : msg)))
          if (convId) {
            api.messages(convId)
              .then((msgs) => {
                setConvMessages(convId, msgs.map(apiToUI))
              })
              .catch(() => {})
          }
        },
      }, undefined, sendAtts.length ? sendAtts : undefined)
    } finally {
      flushDeltasNow()
      flushReasoningNow()
      sendBusyConvsRef.current.delete(busyKey)
      setConvStreaming(convId, false)
      // 073: a stream that died mid-condense must not leave the composer locked.
      patchTurn(convId, { stopStage: 0, toolNames: [], condensing: false, waitLine: null })
      // 089: turn over — freeze any still-spinning chip entries (the log
      // itself survives, collapsed to "🔧 n tools", until the next turn).
      settleToolLog(convId)
      // 012: continuation belongs to the conversation, not the viewport —
      // fire it even if the user switched away (queued drains finish in the
      // background chat instead of dying on switch).
      if (needsContinuation && convId) {
        const contId = convId
        setTimeout(() => triggerContinuation(contId), 400)
      }
      refreshConversations()
      // Slash commands like /identity and /personality mutate the agent's
      // identity server-side; refresh it so the sidebar + settings stay live.
      if (onIdentityChange && /^\/(identity|personality)\b/i.test(text)) {
        api.identity().then(onIdentityChange).catch(() => {})
      }
      syncOnboarding()
    }
  }

  // 045/phase05: stable callbacks + a memoized timeline. renderTimeline used
  // to run inline in JSX, so EVERY state change (composer keystrokes,
  // toolNames, stop stage) re-interleaved and re-rendered the whole timeline.
  const onSecretResolvedCb = useCallback((id: string, status: 'fulfilled' | 'cancelled') => {
    setSecretReqs((prev) =>
      prev.map((r) =>
        r.id === id ? { ...r, status, resolved_at: new Date().toISOString() } : r,
      ),
    )
  }, [])

  const onPlanResume = useCallback(() => {
    // 038/phase04: optimistic "Resuming…" + 60 s safety timeout;
    // failures surface as a system line instead of dying silently.
    if (planResuming) return
    setPlanResuming(true)
    if (planResumingTimer.current !== null) window.clearTimeout(planResumingTimer.current)
    planResumingTimer.current = window.setTimeout(() => setPlanResuming(false), 60_000)
    void api.resumePlan().then((r) => {
      if (r.conversation_id && r.conversation_id !== activeId) {
        selectConversation(r.conversation_id)
      }
    }).catch((e) => {
      clearPlanResuming()
      addSystemLine(activeIdRef.current, `⚠ Resume failed: ${e instanceof Error ? e.message : 'request error'}`)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planResuming, activeId, clearPlanResuming, addSystemLine])

  const onPlanDismiss = useCallback(() => {
    // 043: optimistic dismiss — the card hides NOW; the API call
    // runs behind it and a failure restores the card with a line.
    const prevTasks = planTasks
    const prevCreated = planCreatedAt
    setPlanTasks([])
    setPlanCreatedAt(null)
    void api.dismissPlan().catch(() => {
      setPlanTasks(prevTasks)
      setPlanCreatedAt(prevCreated)
      addSystemLine(activeIdRef.current, '⚠ Dismiss failed — the plan is still open')
    })
  }, [planTasks, planCreatedAt, addSystemLine])

  const timeline = useMemo(() => renderTimeline(
    messages,
    approvals,
    activeId,
    identity?.emoji || '🌙',
    identity?.avatar_url,
    identity?.name,
    debugMode ? debugEvents : null,
    secretReqs,
    onSecretResolvedCb,
    // 026: live plan card — interleaved at the plan's creation time, inline
    // in the timeline (it scrolls with the chat). A completed plan is a
    // persisted kind==="task_plan" message instead; an empty `ui.tasks`
    // clears this one. 045/phase05: no loadingMessages gate — the card paints
    // from the first successful fetch (= 044 Bug 20).
    // The card renders ONLY in its home conversation — other chats never see
    // it. A plan with no conversation_id (legacy rows) falls back to
    // rendering wherever the user is so it stays reachable.
    planConversationId == null || planConversationId === activeId ? planTasks : [],
    planCreatedAt,
    // 043: spinner honesty — animate ONLY off a live signal: this tab's
    // stream, or the server's turn registry saying a turn is running for the
    // plan (covers other conversations/headless). Persisted in_progress rows
    // alone (crashed/paused turn) render a static card with the paused footer.
    streaming || (planInProgress && planTurnActive),
    onPlanResume,
    onPlanDismiss,
    planResuming,
    planCollapsed,
    togglePlanCollapsed,
    onTurnErrorRetryCb,
    streaming,
    // 089: receipts for tools auto-approved during a live turn are the wrench
    // chip's job — suppress their timeline rows (the set mutates in the same
    // tick as the `approvals` updates this memo already keys on).
    liveAutoIdsRef.current,
  ), [
    messages, approvals, activeId,
    identity?.emoji, identity?.avatar_url, identity?.name,
    debugMode, debugEvents, secretReqs, onSecretResolvedCb,
    planTasks, planCreatedAt, planConversationId, streaming, planInProgress, planTurnActive,
    onPlanResume, onPlanDismiss, planResuming, planCollapsed, togglePlanCollapsed, onTurnErrorRetryCb,
  ])

  // 057 phase04: mobile = conversations list as StackPane root, the active
  // conversation as the pushed detail (messaging-app convention — the list
  // keeps the bottom nav reachable; nothing auto-pushes on load).
  const { isMobile } = useViewport()
  // Dense layout = narrow surface (split-pane compact OR a phone).
  const dense = compact || isMobile
  useRegisterMobileDetail(isMobile && mobileVisible && !!activeId)

  const conversationList = (big: boolean) => (
    <>
      <div className="p-3">
        <button
          onClick={newConversation}
          data-testid="new-chat-btn"
          className={cn(
            'w-full inline-flex items-center justify-center gap-2 rounded-lg bg-luna-600 hover:bg-luna-500 transition text-white text-sm font-medium px-3 shadow-lg shadow-luna-900/30',
            big ? 'py-3' : 'py-2',
          )}
        >
          <Plus className="w-4 h-4" />
          New chat
        </button>
      </div>
      <div className="flex-1 overflow-y-auto overflow-x-hidden overscroll-contain px-2 pb-3 space-y-0.5">
        {loadingConvs && (
          <div data-testid="convs-skeleton" className="animate-pulse space-y-0.5 pt-1">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex items-center px-3 py-2">
                <div className="h-3 rounded bg-white/10" style={{ width: `${60 + (i % 3) * 12}%` }} />
              </div>
            ))}
          </div>
        )}
        {!loadingConvs && conversations.length === 0 && (
          <div className="text-xs text-ink-500 px-3 py-2">No conversations yet.</div>
        )}
        {conversations.map((c) => (
          <ConversationItem
            key={c.id}
            conv={c}
            active={!isMobile && activeId === c.id}
            onSelect={() => selectConversation(c.id)}
            big={big}
          />
        ))}
      </div>
    </>
  )

  // 089: the active row's build/operate meta drives the header chip, the
  // hidden delete affordance, and the composer's state pulldown.
  const activeConv = conversations.find((c) => c.id === activeId) ?? null
  const activeKind: ConversationKind | null = activeConv ? convKind(activeConv) : null
  const activeState = activeConv ? convState(activeConv) : null

  const chatArea = (
    <div className="flex-1 flex flex-col min-w-0 min-h-0 relative">
        <ChatHeader
          identity={identity}
          activeId={activeId}
          activeTitle={activeConv?.title || null}
          activeOps={activeKind === 'ops'}
          messages={messages}
          debugMode={debugMode}
          debugEvents={debugEvents}
          approvals={approvals}
          onBack={isMobile ? () => setActiveId(null) : undefined}
          onRenamed={(title) => {
            setConversations((prev) => prev.map((x) => (x.id === activeId ? { ...x, title } : x)))
          }}
          onDeleted={() => {
            if (!activeId) return
            const deletedId = activeId
            setConversations((prev) => prev.filter((x) => x.id !== deletedId))
            const rest = conversations.filter((x) => x.id !== deletedId)
            if (isMobile) setActiveId(null)
            else if (rest.length) selectConversation(rest[0].id)
            else newConversation()
          }}
        />

        {/* 011: top padding moved off the container so alerts can sit flush;
            the spacer below restores the original message offset.
            013: the feedback banner moved out — it docks onto the composer. */}
        <div ref={scrollRef} onScroll={handleScroll} className={cn('flex-1 overflow-y-auto overflow-x-hidden overscroll-contain', dense ? 'px-3 pb-3' : 'px-6 pb-6')}>
          <div aria-hidden className={dense ? 'h-3' : 'h-6'} />
          {loadingMessages && (
            <div className="flex items-center justify-center py-12 text-ink-400 text-sm gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading conversation…
            </div>
          )}
          {!loadingMessages && messages.length === 0 && !compact && (
            <EmptyState
              name={agentName(identity)}
              emoji={identity?.emoji || '🌙'}
              avatarUrl={identity?.avatar_url ?? null}
              ownerName={identity?.owner_name ?? null}
            />
          )}
          <div className={cn(dense ? 'space-y-3' : 'max-w-3xl mx-auto space-y-5')}>
            {timeline}
            {/* 089: ONE fixed-height wrench chip carries all in-turn tool
                activity (count + spinner; click = detail panel). It replaces
                the per-call receipt rows, so the timeline's height stays
                constant while tools run. After the turn it settles to the
                final "🔧 n tools" count until the next turn starts. */}
            <ToolProgressChip
              entries={toolLog[activeId ?? ''] ?? NO_TOOL_LOG}
              streaming={streaming}
            />
            {/* 049/phase02: the ONE subagent status line — shimmer while
                running, settles to a muted summary, never becomes a log. */}
            {/* 073: blocking condense — one shimmer line while the pass runs. */}
            {condensing && (
              <div data-testid="condense-line" className="flex items-center px-1 text-[12px] leading-5">
                <span className="shimmer-text">Condensing conversation…</span>
              </div>
            )}
            {subagent && (
              <div data-testid="subagent-line" className="flex items-center px-1 text-[12px] leading-5">
                <span
                  key={subagent.text}
                  className={cn(
                    'fade-in truncate',
                    subagent.status === 'running' && 'shimmer-text',
                    subagent.status === 'done' && 'text-ink-500',
                    subagent.status === 'aborted' && 'text-rose-400/80',
                  )}
                >
                  {subagent.text}
                </span>
              </div>
            )}
            {/* 069: the wait countdown — shimmer "waiting Ns" ticking down
                client-side; flips to "resuming…" at 0 / on finished, cleared
                by the next visible stream activity. */}
            {waitLine && (
              <div data-testid="wait-line" className="flex items-center gap-1.5 px-1 text-[12px] leading-5">
                <Clock className="w-3.5 h-3.5 text-ink-500 shrink-0" />
                <span className="shimmer-text truncate">
                  {waitLine.resuming || waitLine.until - waitNow <= 0
                    ? 'resuming…'
                    : `waiting ${Math.max(1, Math.ceil((waitLine.until - waitNow) / 1000))}s${
                        waitLine.reason ? ` — ${waitLine.reason}` : ''
                      }`}
                </span>
              </div>
            )}
          </div>
        </div>

        {/* 007.013-fix: anchor the "New messages" pill to the composer's top
            edge (bottom-full) so it always floats just above it, regardless of
            the composer's height (which grows when streaming/tool status show). */}
        <div className="relative">
          {showNewMessages && (
            <button
              onClick={() => {
                scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
                setShowNewMessages(false)
              }}
              className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 z-10
                flex items-center gap-1.5 px-3 py-1.5 rounded-full
                border border-white/10 bg-ink-950/70 backdrop-blur-md
                text-white text-xs font-medium shadow-lg
                hover:bg-ink-950/85 transition-colors cursor-pointer"
            >
              New messages <ChevronDown className="w-3.5 h-3.5" />
            </button>
          )}

          {/* 010 (006): composer widget zone — transparent, self-hiding
              native widgets floating above the message box. Empty widgets
              render null, so the zone contributes nothing when idle. */}
          <div className="max-w-3xl mx-auto px-6 flex flex-col gap-2">
            {composerWidgets.map(({ id, Component }) => <Component key={id} />)}
          </div>

          <Composer
            banner={
              hasFeedback && feedbackAlertConv !== null && feedbackAlertConv === activeId ? (
                <AgentFeedbackBanner
                  onVerdict={(v) => {
                    suppressAlert(feedbackAlertConv)
                    setFeedbackAlertConv(null)
                    window.postMessage(
                      {
                        type: 'luna-navigate',
                        section: 'feedback',
                        target: `compose?verdict=${v}&conversation=${feedbackAlertConv}`,
                      },
                      window.location.origin,
                    )
                  }}
                  onDismiss={() => {
                    suppressAlert(feedbackAlertConv)
                    setFeedbackAlertConv(null)
                  }}
                />
              ) : null
            }
            value={input}
            onChange={setInput}
            onSubmit={send}
            focusRef={composerFocusRef}
            streaming={streaming}
            condensing={condensing}
            stopStage={stopStage}
            onStop={handleStop}
            toolNames={toolNames}
            offline={offline}
            contextStatus={contextStatus}
            staged={staged}
            onAttach={stageFiles}
            onRemoveStaged={removeStaged}
            convKind={activeKind}
            convState={activeState}
            onConvStateChange={(v) => {
              if (activeId) changeConvState(activeId, v)
            }}
          />
        </div>
      </div>
  )

  if (isMobile) {
    return (
      <StackPane
        testId="chat-stack"
        className="flex-1"
        noChrome
        detailKey={activeId}
        onPop={() => setActiveId(null)}
        root={
          <div data-testid="chat-mobile-list" className="flex-1 min-h-0 flex flex-col">
            {conversationList(true)}
          </div>
        }
        detail={activeId ? chatArea : null}
      />
    )
  }

  return (
    <div className="flex h-full">
      {/* Conversation list — hidden in compact (split-layout) mode */}
      {!compact && (
        <div className="w-64 shrink-0 border-r border-white/5 bg-ink-950/30 flex flex-col">
          {conversationList(false)}
        </div>
      )}
      {chatArea}
    </div>
  )
}

// 008.95: attachments on a user bubble — image thumbnails (click to open
// full-size) and file chips. `stored=false` (no storage plugin) renders a
// "not stored" chip: the agent processed the bytes but nothing persisted.
function MessageAttachments({ attachments }: { attachments: AttachmentInfo[] }) {
  return (
    <div className="flex flex-wrap gap-2 mb-1.5" data-testid="message-attachments">
      {attachments.map((a, i) => {
        const url = a.url ?? (a.stored ? `/api/attachments?ref=${encodeURIComponent(a.ref)}` : null)
        if (a.kind === 'image' && url) {
          return (
            <a key={`${a.ref}-${i}`} href={url} target="_blank" rel="noreferrer">
              <img
                src={url}
                alt={a.filename}
                loading="lazy"
                className="rounded-lg max-w-[240px] max-h-[240px] object-contain bg-black/20"
              />
            </a>
          )
        }
        const chip = (
          <span className="inline-flex items-center gap-1.5 rounded-lg bg-white/10 border border-white/15 px-2 py-1 text-[12px]">
            <FileText className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate max-w-[160px]">{a.filename}</span>
            <span className="opacity-60 shrink-0">{a.stored ? fmtSize(a.size_bytes) : 'not stored'}</span>
          </span>
        )
        return url ? (
          <a key={`${a.ref}-${i}`} href={url} target="_blank" rel="noreferrer" className="hover:opacity-80 transition">
            {chip}
          </a>
        ) : (
          <span
            key={`${a.ref}-${i}`}
            title="Processed by the agent but not persisted (no storage plugin installed)"
          >
            {chip}
          </span>
        )
      })}
    </div>
  )
}

// 071/072: the agent's reasoning trace. While `live` (the answer hasn't
// started) it streams inside a dim, height-capped panel — visually distinct
// from the answer. Once done it folds into a bare "Thought Ns" text toggle at
// the bubble's top right.
export const ReasoningBlock = memo(function ReasoningBlock({
  text,
  ms,
  live,
}: {
  text: string
  ms?: number
  live: boolean
}) {
  const [open, setOpen] = useState(false)
  const seconds = Math.max(1, Math.round((ms ?? 0) / 1000))
  const bodyRef = useRef<HTMLDivElement | null>(null)

  // 074: split the streamed text into arrival segments — each new chunk mounts
  // its own span, whose .reasoning-seg animation fades white → dim ink.
  const segsRef = useRef<string[]>([])
  const prevTextRef = useRef('')
  if (live && text !== prevTextRef.current) {
    if (prevTextRef.current && text.startsWith(prevTextRef.current)) {
      segsRef.current = [...segsRef.current, text.slice(prevTextRef.current.length)]
    } else {
      segsRef.current = [text]
    }
    prevTextRef.current = text
  }

  // 072.2: while live, keep the streaming trace pinned to its newest line inside
  // a contained scrollbox, so it reads as a distinct, dimmer "thinking" panel
  // rather than the answer and never pushes the whole timeline around.
  useEffect(() => {
    if (live && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [text, live])

  if (live) {
    return (
      <div className="mb-1.5" data-testid="reasoning-live">
        <div className="flex justify-end mb-1" data-testid="reasoning-pill">
          <span className="text-[11px] text-ink-500 dots">Reasoning {seconds}s</span>
        </div>
        <div
          ref={bodyRef}
          data-testid="reasoning-text"
          className="text-[13px] leading-relaxed text-ink-500 whitespace-pre-wrap max-h-[7.5rem] overflow-y-auto rounded-lg bg-black/30 border border-white/5 px-3 py-2"
        >
          {segsRef.current.map((seg, i) => (
            <span key={i} className="reasoning-seg">{seg}</span>
          ))}
        </div>
      </div>
    )
  }

  // Folded — bare text (no button chrome, no icons) at the bubble's top right;
  // clicking toggles the trace, which opens in the same contained dim panel.
  return (
    <div className="mb-1">
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center gap-0.5 text-[11px] text-ink-500 hover:text-ink-300 transition"
          data-testid="reasoning-pill"
        >
          Thought {seconds}s
          <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
        </button>
      </div>
      {open && (
        <div
          data-testid="reasoning-text"
          className="mt-1 text-[13px] leading-relaxed text-ink-400 whitespace-pre-wrap max-h-[10rem] overflow-y-auto rounded-lg bg-black/30 border border-white/5 px-3 py-2"
        >
          {text}
        </div>
      )}
    </div>
  )
})

/** Hover ⋮ on a bubble's top-right corner: copy the message, and the menu's
 *  footer shows when it was sent. Same clipboard strategy as the transcript
 *  copy (clipboard API, execCommand fallback). */
function MessageMenu({ message }: { message: UIMessage }) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])
  async function doCopy() {
    const text = message.content || ''
    let ok = false
    try {
      await navigator.clipboard.writeText(text)
      ok = true
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      } catch {
        ok = false
      }
    }
    if (ok) {
      setCopied(true)
      setTimeout(() => {
        setCopied(false)
        setOpen(false)
      }, 1200)
    } else {
      setOpen(false)
    }
  }
  const sent = message.created_at ? new Date(message.created_at) : null
  const stamp = sent && !isNaN(sent.getTime())
    ? sent.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
    : null
  return (
    <div ref={menuRef} className="absolute -top-1.5 -right-1.5 z-10">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="Message actions"
        data-testid="message-menu-btn"
        className={cn(
          'p-1 rounded-md border border-white/10 bg-ink-800 text-ink-400 hover:text-ink-200 hover:bg-ink-700 transition-opacity',
          open ? 'opacity-100' : 'opacity-0 group-hover/msg:opacity-100 focus-visible:opacity-100',
        )}
      >
        <MoreVertical className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-[100] mt-1 w-44 rounded-lg bg-ink-800 py-1 shadow-xl border border-white/10">
          <button
            onClick={doCopy}
            className={cn(
              'w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/10',
              copied ? 'text-emerald-400' : 'text-ink-200',
            )}
            data-testid="message-menu-copy"
          >
            {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? 'Copied!' : 'Copy message'}
          </button>
          {stamp && (
            <div className="mt-1 px-3 py-1.5 text-[11px] text-ink-500 border-t border-white/10 flex items-center gap-1.5" data-testid="message-menu-time">
              <Clock className="w-3 h-3" /> {stamp}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Bubble({ message, emoji, avatarUrl }: { message: UIMessage; emoji: string; avatarUrl?: string | null }) {
  const isUser = message.role === 'user'
  // 009.001/phase02 (E12): any non-null source is an automation origin —
  // badge generically, no plugin names in core. One named exception:
  // source="curiosity" is the repeatable reflection channel and gets its own
  // quieter badge (a thought, not an automation run).
  const autoSource = !isUser && message.source ? message.source : null
  const isReflection = autoSource === 'curiosity'
  const isAuto = autoSource !== null && !isReflection
  return (
    <div className={cn('flex gap-3 fade-in group/msg', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser && (
        <div className={cn(
          'w-8 h-8 rounded-full grid place-items-center text-lg shrink-0 mt-0.5 border overflow-hidden',
          isReflection
            ? 'bg-sky-600/20 border-sky-500/30'
            : isAuto
              ? 'bg-violet-600/20 border-violet-500/30'
              : 'bg-luna-600/20 border-luna-500/30',
        )}>
          {isReflection
            ? <Brain className="w-4 h-4 text-sky-400" aria-hidden />
            : isAuto
              ? <Zap className="w-4 h-4 text-violet-400" aria-hidden />
              : <AgentAvatar avatarUrl={avatarUrl} emoji={emoji} displaySize={32} imgClassName="w-full h-full object-cover" />}
        </div>
      )}
      {/* Width caps live on this relative wrapper (min-w-0 keeps the flex
          automatic minimum from undoing them) so the hover ⋮ can overhang the
          bubble's corner without being clipped by its overflow-hidden. */}
      <div
        className={cn(
          'relative min-w-0 max-w-[80%]',
          // 078: a bubble carrying a live run block or a tool card takes the
          // full column (capped at 80%) — code and output need the width; a
          // shrink-to-fit bubble would squeeze them to the iframe's 300px.
          (!!message.live_runs?.length || !!message.embed_iframe) && 'w-full',
        )}
      >
      {!message.pending && !!message.content && <MessageMenu message={message} />}
      <div
        className={cn(
          'max-w-full rounded-2xl px-4 py-2.5 text-[15px] leading-relaxed overflow-hidden break-words',
          isUser
            ? 'bg-luna-600 text-white shadow-lg shadow-luna-900/40'
            : isReflection
              ? 'bg-sky-950/40 border border-sky-500/20 text-ink-100'
              : isAuto
                ? 'bg-violet-950/50 border border-violet-500/20 text-ink-100'
                : 'bg-ink-900/70 border border-white/5 text-ink-100',
          // 031: a message sent while the agent works shows immediately, dimmed
          // until the server acks the queue (then it becomes a normal bubble).
          message.queued && 'opacity-60',
        )}
      >
        {isUser ? (
          <>
            {message.attachments && message.attachments.length > 0 && (
              <MessageAttachments attachments={message.attachments} />
            )}
            {message.content && <div className="whitespace-pre-wrap">{message.content}</div>}
            {message.queued && (
              <div
                data-testid="queued-indicator"
                className="mt-1 text-[11px] text-white/70 flex items-center gap-1"
              >
                <Clock className="w-3 h-3" /> Sending…
              </div>
            )}
          </>
        ) : (
          <>
            {message.reasoning && (
              <ReasoningBlock
                text={message.reasoning}
                ms={message.reasoning_ms}
                live={!!message.pending && !message.content}
              />
            )}
            <div className="prose-luna">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={chatUrlTransform}
                components={chatMarkdownComponents}
              >
                {message.content || (message.pending && !message.reasoning ? '...' : '')}
              </ReactMarkdown>
            </div>
          </>
        )}
        {message.pending && !message.content && !message.reasoning && !message.live_runs?.length && (
          <span className="text-ink-500 text-sm dots">thinking</span>
        )}
        {message.live_runs && message.live_runs.length > 0 && (
          <LiveRunList runs={message.live_runs} />
        )}
        {message.embed_iframe && !message.live_runs?.length && (
          <PluginEmbed html={message.embed_iframe} asIframe />
        )}
        {message.embed_html && !message.embed_iframe && (
          <PluginEmbed html={message.embed_html} asIframe={false} />
        )}
        {isReflection && (
          <div
            data-testid="reflection-badge"
            className="mt-1.5 pt-1.5 border-t border-sky-500/20 text-[11px] text-sky-400 flex items-center gap-1"
          >
            <Brain className="w-3 h-3" aria-hidden /> Reflection
          </div>
        )}
        {isAuto && (
          <div className="mt-1.5 pt-1.5 border-t border-violet-500/20 text-[11px] text-violet-400 flex items-center gap-1">
            <Zap className="w-3 h-3" aria-hidden /> Auto sent from {autoSource} run
          </div>
        )}
      </div>
      </div>
    </div>
  )
}

// 045/phase05: during streaming only the target message object changes per
// flush — every other UIMessage keeps its reference, so memo skips its whole
// subtree (including the ReactMarkdown re-parse, the dominant per-token cost).
// Exported for the render-count probe test.
export const MemoBubble = memo(Bubble)

// 008.994: a muted system→agent message. Collapsed by default as a dark-grey
// disclosure row (`▸ title`); expand to read the full note the system handed the
// agent. Nothing is hidden — it's transparent by design. The agent's reply
// renders as a normal bubble immediately after.
function MutedLine({ title, content }: { title: string; content: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="fade-in">
      <button
        type="button"
        data-testid="muted-message"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-[12px] text-ink-500 hover:text-ink-300 transition-colors px-2 py-1 rounded-md hover:bg-white/[0.03] w-full text-left"
      >
        <span className={cn('transition-transform text-[10px]', open && 'rotate-90')}>▸</span>
        <span className="uppercase tracking-wide text-[11px]">{title}</span>
      </button>
      {open && (
        <div className="ml-5 mt-1 pl-3 border-l border-white/10 text-[13px] text-ink-400 prose-luna">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            urlTransform={chatUrlTransform}
            components={chatMarkdownComponents}
          >
            {content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}

// 036/phase02: a draft the agent was writing when an urgent message or stop
// broke the turn. It was never delivered — the follow-up turn owns the real
// reply — but the record keeps it, collapsed and dimmed, so the timeline
// never shows a silent void and recall can account for the interruption.
function InterruptedDraft({ content }: { content: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex gap-3 fade-in justify-start">
      <div className="w-8 shrink-0" />
      <div className="max-w-[80%]">
        <button
          type="button"
          data-testid="interrupted-draft"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 text-[12px] text-ink-500 hover:text-ink-300 transition-colors px-2 py-1 rounded-md hover:bg-white/[0.03] text-left"
        >
          <span className={cn('transition-transform text-[10px]', open && 'rotate-90')}>▸</span>
          <Hand className="w-3 h-3" aria-hidden />
          <span className="uppercase tracking-wide text-[11px]">interrupted — draft not delivered</span>
        </button>
        {open && (
          <div className="ml-5 mt-1 pl-3 border-l border-white/10 text-[13px] text-ink-400 opacity-70 prose-luna">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              urlTransform={chatUrlTransform}
              components={chatMarkdownComponents}
            >
              {content}
            </ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

// 008.9: a centered muted status line in the timeline (stop / inject acks).
function SystemLine({ text }: { text: string }) {
  return (
    <div className="flex justify-center fade-in">
      <div className="text-[12px] text-ink-500 italic px-3 py-1 rounded-full bg-white/[0.03] border border-white/5">
        {text}
      </div>
    </div>
  )
}

// 008.95: compact "0.9 MB" / "412 KB" label for attachment chips.
function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

// 008.95: one staged file in the composer — image preview or file chip, with
// an upload spinner, error state, and a remove control.
function StagedChip({ item, onRemove }: { item: StagedAttachment; onRemove: () => void }) {
  const busy = item.status === 'uploading' || item.status === 'staged'
  const isImage = item.file.type.startsWith('image/')
  return (
    <div
      data-testid="staged-attachment"
      title={item.status === 'error' ? item.error : item.file.name}
      className={cn(
        'relative group flex items-center gap-2 rounded-lg border px-2 py-1.5 max-w-[220px]',
        item.status === 'error'
          ? 'border-rose-500/40 bg-rose-950/40 text-rose-300'
          : 'border-white/10 bg-ink-800/80 text-ink-200',
      )}
    >
      {isImage && item.previewUrl ? (
        <img src={item.previewUrl} alt={item.file.name} className="w-9 h-9 rounded object-cover shrink-0" />
      ) : (
        <FileText className="w-5 h-5 shrink-0 text-ink-400" />
      )}
      <div className="min-w-0 text-[12px] leading-tight">
        <div className="truncate">{item.file.name}</div>
        <div className="text-[11px] text-ink-500">
          {item.status === 'error' ? (item.error || 'upload failed') : fmtSize(item.file.size)}
        </div>
      </div>
      {busy && <Loader2 className="w-3.5 h-3.5 animate-spin text-luna-300 shrink-0" />}
      <button
        type="button"
        onClick={onRemove}
        title="Remove"
        className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-ink-700 hover:bg-ink-600 border border-white/10 grid place-items-center opacity-0 group-hover:opacity-100 transition"
      >
        <X className="w-2.5 h-2.5" />
      </button>
    </div>
  )
}

/** The composer's "working…" line. Short runs list tool names as text; once
 *  the list would ellipsize it collapses to one wrench chip per distinct tool
 *  (repeat calls get a ×N count) with the name on hover — many tools fit
 *  without truncation. The spinner never shrinks (shrink-0): as a bare flex
 *  child it was the first thing squeezed to micro size by long name lists. */
// 089: the ONE in-turn tool surface in the timeline. A single fixed-height
// chip (spinner + 🔧 + running count) whose DOM height never changes while
// tools run — no per-call rows, no layout jumps. Click toggles a bounded,
// scrollable detail panel listing every call and its status. When the turn
// ends the chip settles to "🔧 n tools" (rose accent if any call errored)
// and stays as the turn's receipt until the next turn starts.
function ToolProgressChip({ entries, streaming }: { entries: ToolLogEntry[]; streaming: boolean }) {
  const [open, setOpen] = useState(false)
  // A fresh turn (log reset) starts collapsed again.
  useEffect(() => {
    if (entries.length === 0) setOpen(false)
  }, [entries.length === 0]) // eslint-disable-line react-hooks/exhaustive-deps
  if (entries.length === 0) return null
  const anyError = entries.some((e) => e.status === 'error')
  const runningEntry = streaming ? [...entries].reverse().find((e) => e.status === 'running') : undefined
  return (
    <div className="px-1">
      <button
        type="button"
        data-testid="tool-progress-chip"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={cn(
          'inline-flex h-7 max-w-full items-center gap-1.5 rounded-full border bg-transparent px-2.5 text-[11px] leading-none transition hover:bg-white/[0.04]',
          anyError
            ? 'border-rose-500/40 text-rose-300'
            : streaming
              ? 'border-luna-500/40 text-luna-300'
              : 'border-white/10 text-ink-400',
        )}
        title={streaming ? 'Tools running — click for details' : 'Tools used this turn — click for details'}
      >
        {streaming && <Loader2 className="w-3 h-3 shrink-0 animate-spin" />}
        <span aria-hidden>🔧</span>
        <span data-testid="tool-progress-count">
          {streaming ? entries.length : `${entries.length} tool${entries.length === 1 ? '' : 's'}`}
        </span>
        {runningEntry && (
          <span className="truncate max-w-[16rem] text-ink-400">{runningEntry.name}</span>
        )}
        <ChevronDown className={cn('w-3 h-3 shrink-0 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          data-testid="tool-progress-panel"
          className="mt-1 max-h-44 max-w-md overflow-y-auto rounded-lg border border-white/10 bg-ink-900/80 px-2.5 py-1.5 text-[11px]"
        >
          {entries.map((e, i) => (
            <div key={i} className="flex items-center gap-1.5 leading-5 min-w-0">
              {e.status === 'running' ? (
                <Loader2 className="w-3 h-3 shrink-0 animate-spin text-luna-300" />
              ) : e.status === 'error' ? (
                <X className="w-3 h-3 shrink-0 text-rose-400" />
              ) : (
                <Check className="w-3 h-3 shrink-0 text-emerald-400" />
              )}
              <span className={cn('truncate', e.status === 'error' ? 'text-rose-300' : 'text-ink-300')}>
                {e.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function WorkingTools({ toolNames }: { toolNames: string[] }) {
  const compact = toolNames.length > 3 || toolNames.join(', ').length > 34
  const counts = new Map<string, number>()
  for (const n of toolNames) counts.set(n, (counts.get(n) ?? 0) + 1)
  return (
    <>
      <Loader2 className="w-3 h-3 shrink-0 animate-spin text-luna-300" />
      <span className="text-luna-300 shrink-0">working…</span>
      {compact ? (
        <div className="flex items-center gap-1 min-w-0 overflow-hidden" data-testid="working-tool-chips">
          {[...counts.entries()].map(([name, n]) => (
            <span
              key={name}
              title={n > 1 ? `${name} ×${n}` : name}
              className="inline-flex items-center gap-0.5 px-1 py-0.5 rounded-md bg-white/[0.06] text-luna-300 shrink-0"
            >
              <Wrench className="w-3 h-3" />
              {n > 1 && <span className="text-[10px] leading-none">{n}</span>}
            </span>
          ))}
        </div>
      ) : (
        <span className="text-luna-300 truncate">{toolNames.join(', ')}</span>
      )}
    </>
  )
}

function Composer({
  value, onChange, onSubmit, streaming, condensing = false, stopStage, onStop,
  toolNames, offline, contextStatus, staged, onAttach, onRemoveStaged,
  focusRef, banner, convKind = null, convState = null, onConvStateChange,
}: {
  /** 013: alert tab docked onto the box's top edge (e.g. agent feedback). */
  banner?: React.ReactNode
  value: string
  onChange: (v: string) => void
  onSubmit: () => void
  streaming: boolean
  /** 073: a condense pass is running — input is blocked until it finishes. */
  condensing?: boolean
  stopStage: 0 | 1 | 2
  onStop: () => void
  toolNames: string[]
  offline: boolean
  contextStatus?: ContextStatus | null
  staged: StagedAttachment[]
  onAttach: (files: File[]) => void
  onRemoveStaged: (id: string) => void
  /** 065: chat bridge — receives a thunk that focuses the textarea. */
  focusRef?: React.MutableRefObject<(() => void) | null>
  /** 089: active conversation's kind — null hides the state pulldown (no conversation yet). */
  convKind?: ConversationKind | null
  /** 089: active conversation's effective state (defaulted per kind). */
  convState?: string | null
  /** 089: the user picked a state — the ONLY trigger for a state write. */
  onConvStateChange?: (value: string) => void
}) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [dragOver, setDragOver] = useState(false)
  // Send is armed by text OR a usable attachment; a still-uploading file
  // arms it too (send() awaits the upload).
  const hasSendable = !!value.trim() || staged.some((x) => x.status !== 'error')

  function handleKey(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSubmit()
    }
  }
  // 008.95: paste an image (or any file) straight into the composer.
  function handlePaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(e.clipboardData?.files ?? [])
    if (files.length) {
      e.preventDefault()
      onAttach(files)
    }
  }
  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault()
    setDragOver(false)
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length) onAttach(files)
  }
  // 043 item 4: the placeholder names the actual agent (live from the
  // identity store), never a hardcoded default.
  const name = agentName(useIdentity())
  // 043 item 5: autosize upward — the box grows with the input to 2× its
  // default height (44px → 88px); only past that does an internal scrollbar
  // appear. Clearing the value (send) snaps it back to one line.
  const taRef = useRef<HTMLTextAreaElement | null>(null)
  useEffect(() => {
    if (!focusRef) return
    focusRef.current = () => taRef.current?.focus()
    return () => { focusRef.current = null }
  }, [focusRef])
  useLayoutEffect(() => {
    const el = taRef.current
    if (!el) return
    const max = 88 // 2× the 44px single-line default
    el.style.height = 'auto'
    el.style.height = `${Math.max(44, Math.min(el.scrollHeight, max))}px`
    el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
  }, [value])
  // 010 (006): no hairline, no dark slab — the composer floats over the
  // chat; the visible box below keeps its own background.
  return (
    <div className="px-6 py-4">
      <div className="max-w-3xl mx-auto">
        {/* 013: docked alert tab — sits on the box's flat top edge. */}
        {banner}
        {/* The textarea gets its own row; attach + Send/Stop live on a row
            BELOW it (they used to overlay the box's corner and collide with
            the text). */}
        <div
          className={cn(
            'bg-ink-900/70 border border-white/10 rounded-2xl shadow-2xl shadow-luna-900/10 focus-within:border-luna-500/50 focus-within:ring-2 focus-within:ring-luna-500/20 transition',
            dragOver && 'border-luna-500/60 ring-2 ring-luna-500/30',
          )}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        >
          {/* 008.95: staged attachment chips above the input. */}
          {staged.length > 0 && (
            <div className="flex flex-wrap gap-2 px-3 pt-3">
              {staged.map((it) => (
                <StagedChip key={it.id} item={it} onRemove={() => onRemoveStaged(it.id)} />
              ))}
            </div>
          )}
          <textarea
            ref={taRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKey}
            onPaste={handlePaste}
            placeholder={condensing ? 'Condensing conversation…' : streaming ? `Message ${name} — send anytime, it's read mid-turn…` : `Message ${name}…  (Shift+Enter for newline)`}
            disabled={condensing}
            rows={1}
            className="block w-full bg-transparent resize-none px-4 py-3 text-ink-50 placeholder-ink-500 outline-none"
            style={{ minHeight: 44 }}
          />
          {/* 031 WhatsApp-style: while streaming show Stop, but also a Send
              the moment there's text to fire — the user can pile on messages
              any time and Luna ingests each at the next step boundary. The
              working/offline status shares this row, left of the buttons. */}
          <div className="flex items-center justify-end gap-2 px-2 pb-2">
            {/* 014: the agent-state picker lives INSIDE the box, bottom-left —
                it sets what the AGENT may do in this chat. User-driven only. */}
            {convKind && convState && onConvStateChange && (
              <StatePickerMenu kind={convKind} value={convState} onChange={onConvStateChange} />
            )}
            <div className="flex-1 text-[11px] text-ink-500 flex items-center gap-2 min-w-0 pl-1">
              {toolNames.length > 0 ? (
                <WorkingTools toolNames={toolNames} />
              ) : offline ? (
                <span className="text-rose-400">offline — reconnecting…</span>
              ) : null}
            </div>
            {/* 008.95: attach files (paperclip). Paste and drag-drop work too. */}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              onChange={(e) => {
                const files = Array.from(e.target.files ?? [])
                if (files.length) onAttach(files)
                e.target.value = ''
              }}
            />
            <button
              type="button"
              data-testid="attach-button"
              onClick={() => fileInputRef.current?.click()}
              disabled={offline}
              title="Attach files (or paste / drop them)"
              className="inline-flex items-center justify-center rounded-lg text-ink-400 hover:text-ink-200 hover:bg-white/[0.06] transition p-2 disabled:opacity-40"
            >
              <Paperclip className="w-4 h-4" />
            </button>
            {streaming && hasSendable && (
              <button
                onClick={onSubmit}
                disabled={offline}
                title={`Send now — ${name} reads it mid-turn`}
                className="inline-flex items-center gap-1.5 rounded-lg bg-luna-600 hover:bg-luna-500 disabled:bg-ink-800 disabled:text-ink-500 transition text-white text-sm font-medium py-1 px-3 max-md:py-2"
              >
                <Send className="w-3.5 h-3.5" />
                Send
              </button>
            )}
            {streaming ? (
              <button
                onClick={onStop}
                title={stopStage >= 1 ? 'Hard-stop now' : 'Stop after the current step'}
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-lg transition text-white text-sm font-medium py-1 px-3 max-md:py-2',
                  stopStage >= 1
                    ? 'bg-rose-600 hover:bg-rose-500 shadow-lg shadow-rose-900/40'
                    : 'bg-ink-700 hover:bg-ink-600',
                )}
              >
                <Square className="w-3.5 h-3.5" fill="currentColor" />
                {stopStage >= 1 ? 'Hard stop' : 'Stop'}
              </button>
            ) : (
              <button
                onClick={onSubmit}
                disabled={offline || condensing || !hasSendable}
                className="inline-flex items-center gap-1.5 rounded-lg bg-luna-600 hover:bg-luna-500 disabled:bg-ink-800 disabled:text-ink-500 transition text-white text-sm font-medium py-1 px-3 max-md:py-2"
              >
                <Send className="w-3.5 h-3.5" />
                Send
              </button>
            )}
          </div>
        </div>
        {/* 008: model selector + context meter live OUTSIDE the message box. */}
        <div className="flex items-center px-1 pt-1.5">
          <ComposerModelSelect />
          {/* 014 (ops only): amber capability line — what the current state
              permits, live with state changes. Hover = one paragraph of depth. */}
          {convKind === 'ops' && convState && <OpsCapabilityLine state={convState} />}
          {contextStatus && (
            <div className="ml-auto">
              <ContextMeter status={contextStatus} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// 014: agent-state picker — a custom upward menu in the model selector's
// visual family (dark panel, eyebrow header, one-line rows), replacing the
// 0.17.0 native <select>. Closed chip: hairline neutral border (amber TEXT in
// ops); the amber/luna border shows only while the menu is open or the chip
// is focused. Selection is the ONLY state-write trigger (never programmatic).
const STATE_DETAILS_KEY = 'luna.chat.stateDetails'

function StatePickerMenu({ kind, value, onChange }: {
  kind: ConversationKind
  value: string
  onChange: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [focused, setFocused] = useState(false)
  // 015 (ops only): "Details" expander — per-option can/won't bullets that
  // say what THIS agent works on (Luna herself). Remembered per browser.
  const [details, setDetails] = useState<boolean>(() => {
    try { return localStorage.getItem(STATE_DETAILS_KEY) === '1' } catch { return false }
  })
  const toggleDetails = () => {
    setDetails((v) => {
      const next = !v
      try { localStorage.setItem(STATE_DETAILS_KEY, next ? '1' : '0') } catch { /* unavailable */ }
      return next
    })
  }
  const rootRef = useRef<HTMLDivElement | null>(null)

  // Same close behavior as the model picker: click-outside + Esc.
  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const options = STATE_OPTIONS[kind]
  const current = options.find((o) => o.value === value) ?? options[0]
  const accent = open || focused
  return (
    <div className="relative shrink-0" ref={rootRef}>
      <button
        type="button"
        data-testid="state-pulldown"
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        aria-haspopup="listbox"
        aria-expanded={open}
        title="Agent state — what the agent may do in this chat"
        className={cn(
          'inline-flex items-center gap-1 rounded-md border bg-transparent px-1.5 py-0.5 text-[11px] outline-none transition hover:bg-white/[0.04]',
          accent
            ? kind === 'ops' ? 'border-amber-500/60' : 'border-luna-500/50'
            : 'border-white/10',
          kind === 'ops'
            ? 'text-amber-300'
            : value !== DEFAULT_STATE[kind] ? 'text-luna-300' : 'text-ink-400',
        )}
      >
        <span className="truncate max-w-[11rem]">{current.label}</span>
        <ChevronDown className={cn('w-3 h-3 opacity-70 transition', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          data-testid="state-menu"
          role="listbox"
          aria-label="Agent state"
          className={cn(
            'absolute bottom-full mb-2 left-0 z-50 fade-in',
            'w-[min(19rem,calc(100vw-1.5rem))] rounded-2xl border border-white/10 bg-ink-900/95 backdrop-blur',
            'shadow-2xl shadow-black/40 py-1.5',
          )}
        >
          <div className="flex items-center px-3.5 pt-2 pb-1.5 text-[11px] uppercase tracking-[0.16em] font-semibold text-ink-500 border-b border-white/5">
            <span>AGENT STATE</span>
            {kind === 'ops' && (
              <button
                type="button"
                data-testid="state-details-toggle"
                aria-expanded={details}
                onClick={(e) => { e.stopPropagation(); toggleDetails() }}
                className="ml-auto inline-flex items-center gap-0.5 normal-case tracking-normal font-medium text-[11px] text-amber-300/80 hover:text-amber-200 outline-none focus-visible:underline"
              >
                Details
                <ChevronDown className={cn('w-3 h-3 transition', details && 'rotate-180')} aria-hidden />
              </button>
            )}
          </div>
          {kind === 'ops' && details && (
            <div data-testid="state-details-intro" className="px-3.5 pt-2 pb-1 text-[10.5px] leading-snug text-ink-300">
              {OPS_AGENT_INTRO}
            </div>
          )}
          <ul className="py-1">
            {options.map((o) => {
              const active = o.value === value
              return (
                <li key={o.value} role="option" aria-selected={active}>
                  <div
                    tabIndex={0}
                    data-testid={`state-option-${o.value}`}
                    onClick={() => {
                      setOpen(false)
                      if (!active) onChange(o.value)
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setOpen(false)
                        if (!active) onChange(o.value)
                      }
                    }}
                    className={cn(
                      'w-full flex items-start gap-2 px-3.5 py-2 text-left cursor-pointer transition hover:bg-white/10',
                      active && (kind === 'ops'
                        ? 'bg-amber-500/15 ring-1 ring-inset ring-amber-400/50'
                        : 'bg-luna-500/25 ring-1 ring-inset ring-luna-400/60'),
                    )}
                  >
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          'block text-[12px] font-semibold',
                          active ? (kind === 'ops' ? 'text-amber-200' : 'text-luna-100') : 'text-ink-100',
                        )}
                      >
                        {o.label}
                      </span>
                      <span className="block text-[10px] leading-snug text-ink-500 mt-0.5">{o.desc}</span>
                      {kind === 'ops' && details && OPS_STATE_DETAILS[o.value] && (
                        <OpsStateBullets
                          detail={OPS_STATE_DETAILS[o.value]}
                          testId={`state-details-${o.value}`}
                          className="mt-1.5"
                        />
                      )}
                    </span>
                    {active && (
                      <Check
                        className={cn('w-3.5 h-3.5 shrink-0 mt-0.5', kind === 'ops' ? 'text-amber-300' : 'text-luna-300')}
                        aria-hidden
                      />
                    )}
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

// 014 (ops only): the capability line right of the model selector — restates
// what the current state permits, amber, with a dotted underline as the
// visible hover affordance. The tooltip is depth, not the primary surface:
// the line alone carries the bottom line (ux_guidelines §6).
function OpsCapabilityLine({ state }: { state: string }) {
  const [tip, setTip] = useState(false)
  const text = OPS_CAPABILITY[state]
  const current = STATE_OPTIONS.ops.find((o) => o.value === state)
  const detail = OPS_STATE_DETAILS[state]
  if (!text) return null
  return (
    <span
      data-testid="ops-capability-line"
      tabIndex={0}
      onMouseEnter={() => setTip(true)}
      onMouseLeave={() => setTip(false)}
      onFocus={() => setTip(true)}
      onBlur={() => setTip(false)}
      className="relative ml-2 shrink min-w-0 truncate text-[11px] text-amber-300/90 underline decoration-dotted decoration-amber-500/50 underline-offset-2 cursor-help outline-none"
    >
      {text}
      {tip && (
        <span
          role="tooltip"
          data-testid="ops-capability-tooltip"
          className="absolute bottom-full left-0 mb-1.5 z-50 block w-80 whitespace-normal rounded-lg border border-white/15 bg-ink-950 px-2.5 py-2 text-left text-[10px] leading-snug text-ink-200 shadow-xl"
        >
          <span className="block">{OPS_CAPABILITY_TOOLTIP}</span>
          {current && (
            <span className="block mt-1.5 text-amber-200 font-semibold">
              {current.label}
              <span className="font-normal text-ink-300"> — {current.desc}</span>
            </span>
          )}
          {detail && <OpsStateBullets detail={detail} testId="ops-capability-tooltip-details" className="mt-1" />}
        </span>
      )}
    </span>
  )
}

// 015: the can/won't bullets for one ops state — shared by the state menu's
// Details expander and the capability line's tooltip.
function OpsStateBullets({ detail, testId, className }: {
  detail: { can: string[]; wont: string }
  testId: string
  className?: string
}) {
  return (
    <span data-testid={testId} className={cn('block text-[10px] leading-snug', className)}>
      {detail.can.map((line) => (
        <span key={line} className="flex gap-1.5 text-ink-300">
          <span className="text-emerald-400 shrink-0" aria-hidden>+</span>
          <span>{line}</span>
        </span>
      ))}
      <span className="flex gap-1.5 text-ink-400">
        <span className="text-ink-500 shrink-0" aria-hidden>–</span>
        <span>{detail.wont}</span>
      </span>
    </span>
  )
}

// 008.005: compact reasoning-model picker at the composer bottom-left.
// Edits the global `reasoning` chain head (same setting as Settings → Models),
// preserving the fallback tail + policy. User-initiated, ungated.
function ComposerModelSelect() {
  const [chains, setChains] = useState<ModelChain[] | null>(null)
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null)
  const [configured, setConfigured] = useState<string[]>([])
  // 064: per-model context-window caps ("provider:model" → tokens).
  // 067: undefined = the core didn't advertise caps support (no `window_caps`
  // in its catalog response) — the picker then hides the window pulldown
  // entirely instead of rendering a control that can't read or persist.
  const [windowCaps, setWindowCaps] = useState<Record<string, number> | undefined>(undefined)
  const [saving, setSaving] = useState(false)

  const load = useCallback(() => {
    api.models().then(setChains).catch(() => setChains([]))
    api
      .modelCatalog()
      .then((c) => {
        setCatalog(c.catalog.reasoning)
        setConfigured(c.configured_providers)
        setWindowCaps(c.window_caps)
      })
      .catch(() => setCatalog([]))
  }, [])

  // 045/phase05: the picker is below the fold of what first paint needs —
  // defer its two fetches (models + catalog) to browser idle time so they
  // don't compete with the conversations/messages fetches on load.
  useEffect(() => {
    const w = window as Window & {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number
      cancelIdleCallback?: (id: number) => void
    }
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(load, { timeout: 2000 })
      return () => w.cancelIdleCallback?.(id)
    }
    const t = window.setTimeout(load, 300)
    return () => window.clearTimeout(t)
  }, [load])

  const reasoning = chains?.find((c) => c.purpose === 'reasoning')
  const currentFqns = reasoning?.chain.map((e) => `${e.provider}:${e.model}`) ?? []
  const currentHead = currentFqns[0] ?? ''
  const options = (catalog ?? []).map((o) => ({
    ...o,
    unavailable: !configured.includes(o.provider),
  }))
  const anyAvailable = options.some((o) => !o.unavailable)

  // Hide entirely when there's nothing to pick (no configured models / not loaded).
  if (!reasoning || !anyAvailable) return null

  async function onChange(fqn: string) {
    if (!reasoning || fqn === currentHead) return
    const prev = chains
    const nextChain = reorderChainHead(currentFqns, fqn)
    // Optimistic head swap.
    setChains((cs) =>
      (cs ?? []).map((c) =>
        c.purpose === 'reasoning'
          ? {
              ...c,
              chain: nextChain.map((f) => {
                const [provider, model] = f.split(':')
                return { provider, model }
              }),
            }
          : c,
      ),
    )
    setSaving(true)
    try {
      const res = await api.setModelChain('reasoning', nextChain, reasoning.fallback_policy ?? undefined)
      if (!res.updated) {
        setChains(prev ?? null)
      } else {
        load()
      }
    } catch {
      setChains(prev ?? null)
    } finally {
      setSaving(false)
    }
  }

  // 064: persist a per-model window cap (null clears back to Max). Optimistic;
  // caps-only write (empty chain) leaves the chain untouched.
  async function onWindowCapChange(fqn: string, cap: number | null) {
    if (windowCaps == null) return // core without caps support — pulldown hidden anyway
    const prev = windowCaps
    setWindowCaps((cs) => {
      const next = { ...cs }
      if (cap === null) delete next[fqn]
      else next[fqn] = cap
      return next
    })
    try {
      const res = await api.setModelChain('reasoning', [], undefined, { [fqn]: cap })
      if (!res.updated) setWindowCaps(prev)
    } catch {
      setWindowCaps(prev)
    }
  }

  return (
    <ModelPickerMenu
      options={options}
      value={currentHead}
      disabled={saving}
      onChange={(fqn) => void onChange(fqn)}
      windowCaps={windowCaps}
      onWindowCapChange={(fqn, cap) => void onWindowCapChange(fqn, cap)}
      testId="composer-model-select"
    />
  )
}

// 008.004: clickable context meter → breakdown popover (Cursor-style).
function ContextMeter({ status }: { status: ContextStatus }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])
  const hasSections = !!status.sections && status.sections.length > 0
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => hasSections && setOpen((v) => !v)}
        className={cn(
          'flex items-center gap-1.5 text-[10px] text-ink-500 rounded-md px-1 py-0.5 transition',
          hasSections && 'hover:text-ink-300 hover:bg-white/5 cursor-pointer',
        )}
        data-testid="context-meter"
        aria-haspopup={hasSections ? 'dialog' : undefined}
        aria-expanded={open}
      >
        <ContextRing fraction={status.fraction} />
        <span>{Math.round(status.fraction * 100)}% context</span>
      </button>
      {open && hasSections && <ContextBreakdownPopover status={status} />}
    </div>
  )
}

// Stable color per section key.
const SECTION_COLORS: Record<string, string> = {
  system_prompt: '#a78bfa',
  tools: '#38bdf8',
  skills: '#34d399',
  mcp: '#f472b6',
  plugins: '#fbbf24',
  memories: '#22d3ee',
  condensed: '#c084fc',
  conversation: '#60a5fa',
  other: '#64748b',
}

function fmtTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k`
  return String(n)
}

// Per-section help shown via an (i) tooltip. Written so the user can act on it.
const SECTION_HELP: Record<string, string> = {
  other:
    "Everything the model counts beyond the per-section estimates: the formatting overhead of each message and tool call, plus the gap between the quick estimate and the model's exact tokenizer. It grows with long, tool-heavy conversations. Start a new chat to reset it — older messages are also auto-condensed as you approach the limit.",
  condensed:
    'Older messages that were automatically summarized to keep the conversation inside the model’s context window. The agent reads this compact recap instead of the full text, and can still look up exact earlier wording on demand.',
}

// Small info icon with a hover tooltip, used to explain a breakdown row.
function SectionInfo({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex shrink-0" data-testid="section-info">
      <Info className="w-3.5 h-3.5 text-ink-500 hover:text-ink-200 cursor-help" />
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 bottom-full z-10 mb-1.5 hidden w-72 group-hover:block
          rounded-lg border border-white/10 bg-ink-950 px-3 py-2 text-[13px] leading-relaxed text-ink-200 shadow-xl shadow-black/50"
      >
        {text}
      </span>
    </span>
  )
}

function ContextBreakdownPopover({ status }: { status: ContextStatus }) {
  const sections = status.sections || []
  // Scale segments to the FULL context window so the empty/unused space is
  // visible — otherwise the bar always looks full even at 10% usage.
  const windowTotal = status.limit || sections.reduce((a, s) => a + s.tokens, 0) || 1
  const pct = Math.round(status.fraction * 100)
  return (
    <div
      role="dialog"
      data-testid="context-breakdown"
      className="absolute bottom-full right-0 mb-2 w-[min(46rem,calc(100vw-2rem))] rounded-2xl border border-white/10 bg-ink-900/95 backdrop-blur shadow-2xl shadow-black/40 p-5 z-50 fade-in"
    >
      <div className="flex items-baseline justify-between mb-3">
        <div className="text-lg font-semibold text-ink-50">{pct}% Full</div>
        <div className="text-sm text-ink-500 tabular-nums">
          ~{fmtTokens(status.used_tokens)} / {fmtTokens(status.limit)} tokens
        </div>
      </div>
      {/* stacked bar — track shows the empty space, segments the used portion */}
      <div className="flex h-3.5 w-full overflow-hidden rounded-full bg-white/[0.07] mb-4">
        {sections.map((s) => (
          <div
            key={s.key}
            style={{
              width: `${(s.tokens / windowTotal) * 100}%`,
              backgroundColor: SECTION_COLORS[s.key] || '#64748b',
            }}
            title={`${s.label}: ${fmtTokens(s.tokens)}`}
          />
        ))}
      </div>
      {/* legend */}
      <div className="space-y-2">
        {sections.map((s) => (
          <div key={s.key} className="flex items-center justify-between text-sm">
            <div className="flex items-center gap-2.5 min-w-0">
              <span
                className="inline-block w-3 h-3 rounded-sm shrink-0"
                style={{ backgroundColor: SECTION_COLORS[s.key] || '#64748b' }}
              />
              <span className="text-ink-200">{s.label}</span>
              {s.count != null && (
                <span className="text-ink-600 tabular-nums shrink-0">{s.count} tools</span>
              )}
              {SECTION_HELP[s.key] && <SectionInfo text={SECTION_HELP[s.key]} />}
            </div>
            <span className="text-ink-300 tabular-nums shrink-0 ml-3">{fmtTokens(s.tokens)}</span>
          </div>
        ))}
      </div>
      {status.pending_condense && (
        <div
          className="mt-3 pt-2.5 border-t border-white/5 text-xs text-amber-400/90"
          data-testid="pending-condense-hint"
        >
          Over the condense threshold — older messages will be summarized
          automatically after the next reply.
        </div>
      )}
      {status.estimated && (
        <div className="mt-3 pt-2.5 border-t border-white/5 text-xs text-ink-500">
          Estimated — refines to exact counts after your next message.
        </div>
      )}
    </div>
  )
}

// 007.007: small SVG ring showing how full the model's context window is.
// Mirrors Cursor's gauge: fills clockwise, turns amber ≥75% and rose ≥90%.
function ContextRing({ fraction }: { fraction: number }) {
  const pct = Math.max(0, Math.min(1, fraction))
  const size = 14
  const stroke = 2
  const r = (size - stroke) / 2
  const c = 2 * Math.PI * r
  const color = pct >= 0.9 ? 'text-rose-400' : pct >= 0.75 ? 'text-amber-400' : 'text-luna-400'
  const used = Math.round(pct * 100)
  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={cn('shrink-0', color)}
      role="img"
      aria-label={`Context ${used}% full`}
    >
      <title>{`Context window ${used}% full`}</title>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={stroke} className="text-white/10" opacity={0.25} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="currentColor"
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={c}
        strokeDashoffset={c * (1 - pct)}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  )
}

function EmptyState({ name, emoji, avatarUrl, ownerName }: { name: string; emoji: string; avatarUrl: string | null; ownerName: string | null }) {
  return (
    <div className="h-full grid place-items-center">
      <div className="text-center fade-in">
        <div className="text-7xl mb-4 flex justify-center">
          <AgentAvatar avatarUrl={avatarUrl} emoji={emoji} displaySize={96} priority imgClassName="w-24 h-24 rounded-3xl object-cover shadow-lg shadow-luna-950/40" />
        </div>
        <h2 className="text-3xl font-semibold luna-text mb-2">
          {ownerName ? `Hi ${ownerName}, I'm ${name}.` : `Hi, I'm ${name}.`}
        </h2>
        <p className="text-ink-400 max-w-md mx-auto">
          Start typing below — I'll stream a reply. Try <span className="font-mono text-ink-300">/help</span> for commands,
          or tweak my personality under Settings.
        </p>
      </div>
    </div>
  )
}

// 005.917: chat header with conversation actions menu (copy/rename/delete).
// 011/013: one-line feedback alert docked to the composer's top edge — a tab
// narrower than the box (clear of its rounded corners), top corners only, no
// bottom border; -mb-px + solid bg paint over the box's top border so the
// seam between the tab and the message box has no dark line.
export function AgentFeedbackBanner({
  onVerdict,
  onDismiss,
}: {
  onVerdict: (verdict: 'good' | 'mediocre' | 'bad') => void
  onDismiss: () => void
}) {
  return (
    <div data-testid="agent-feedback-alert" className="relative z-10 px-6 -mb-px">
      <div className="mx-auto w-fit max-w-full flex items-center gap-2 bg-ink-900 border border-b-0 border-white/10 rounded-t-lg px-3 py-1.5">
        <span className="text-[13px] text-ink-200 truncate">Is the agent doing a good job?</span>
        {(['good', 'mediocre', 'bad'] as const).map((v) => (
          <button
            key={v}
            data-testid={`agent-feedback-${v}`}
            onClick={() => onVerdict(v)}
            className="text-[12px] px-2 py-0.5 rounded-full border border-white/10 text-ink-200 hover:bg-white/10 whitespace-nowrap capitalize"
          >
            {v}
          </button>
        ))}
        <button
          data-testid="agent-feedback-dismiss"
          aria-label="Dismiss"
          onClick={onDismiss}
          className="ml-1 shrink-0 text-ink-500 hover:text-ink-200"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}

async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Fallback: clipboard API requires document focus; execCommand doesn't.
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      document.body.removeChild(ta)
      return ok
    } catch {
      return false
    }
  }
}

function ChatHeader({
  identity,
  activeId,
  activeTitle,
  activeOps = false,
  messages,
  debugMode,
  debugEvents,
  approvals,
  onBack,
  onRenamed,
  onDeleted,
}: {
  identity: Identity | null
  activeId: string | null
  activeTitle: string | null
  /** 089: the open conversation is an ops conversation — amber chip, no delete (server 403s it). */
  activeOps?: boolean
  messages: UIMessage[]
  debugMode: boolean
  debugEvents: DebugEvent[]
  approvals: ApprovalRecord[]
  /** 057: mobile stack — renders a back chevron that pops to the conversation list. */
  onBack?: () => void
  onRenamed: (title: string) => void
  onDeleted: () => void
}) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [draft, setDraft] = useState(activeTitle || '')
  const [copied, setCopied] = useState(false)
  const [ctxState, setCtxState] = useState<'idle' | 'busy' | 'copied'>('idle')
  const inputRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (renaming) inputRef.current?.focus()
  }, [renaming])

  // Close the menu on outside click. A document listener is immune to
  // z-index/stacking-context bugs that a backdrop overlay div is prone to
  // (an overlay above the menu silently swallows every click).
  useEffect(() => {
    if (!menuOpen) return
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [menuOpen])

  async function doRename() {
    const t = draft.trim()
    if (!t || !activeId) { setRenaming(false); return }
    await api.renameConversation(activeId, t)
    onRenamed(t)
    setRenaming(false)
  }

  async function doDelete() {
    if (!activeId) return
    if (!confirm('Delete this conversation?')) return
    await api.deleteConversation(activeId)
    onDeleted()
  }

  async function doCopy() {
    // 005.913: copy is *context-aware*. With debug mode off, the user gets
    // a clean prose transcript suitable for pasting into a doc or DM. With
    // debug mode on, they get the full timeline (bubbles + tool calls +
    // bus events + approvals) — perfect for bug reports.
    const text = debugMode
      ? renderDebugTranscript(messages as BubbleMessage[], debugEvents, identity, activeId)
      : renderCleanTranscript(
          messages as BubbleMessage[],
          identity,
          approvals
            .filter((a) => a.req.conversation_id === activeId)
            .map((a) => a.req),
        )
    if (await copyToClipboard(text)) {
      setCopied(true)
      setTimeout(() => {
        setCopied(false)
        setMenuOpen(false)
      }, 1200)
    } else {
      alert('Could not copy to clipboard')
      setMenuOpen(false)
    }
  }

  async function doCopyContext() {
    // 087: copy the agent's whole assembled context (system prompt + tool
    // schemas + the history the next turn would send) from the core endpoint.
    if (!activeId || ctxState === 'busy') return
    setCtxState('busy')
    try {
      const t = getToken()
      const r = await fetch(`/api/conversations/${activeId}/context/text`, {
        headers: t ? { Authorization: `Bearer ${t}` } : undefined,
      })
      if (r.status === 404) {
        alert('Copy agent context needs a newer Luna core (0.85.002+).')
        setCtxState('idle')
        setMenuOpen(false)
        return
      }
      if (!r.ok) throw new Error(`${r.status}`)
      const text = String((await r.json()).text ?? '')
      if (text && (await copyToClipboard(text))) {
        setCtxState('copied')
        setTimeout(() => {
          setCtxState('idle')
          setMenuOpen(false)
        }, 1200)
      } else {
        alert('Could not copy the agent context')
        setCtxState('idle')
        setMenuOpen(false)
      }
    } catch {
      alert('Could not fetch the agent context')
      setCtxState('idle')
      setMenuOpen(false)
    }
  }

  return (
    <div className={cn('h-14 border-b flex items-center', onBack ? 'px-2' : 'px-5', debugMode ? 'border-amber-500/30 bg-amber-950/20' : 'border-white/5')}>
      <div className="flex items-center gap-2 flex-1 min-w-0">
        {onBack && (
          <button
            onClick={onBack}
            aria-label="Back to conversations"
            data-testid="chat-back-btn"
            className="w-11 h-11 -my-1 flex items-center justify-center rounded-lg text-ink-300 hover:text-ink-50 hover:bg-white/5 active:bg-white/10 transition shrink-0"
          >
            <ChevronLeft className="w-6 h-6" />
          </button>
        )}
        <div className="text-xl grid place-items-center">
          <AgentAvatar avatarUrl={identity?.avatar_url} emoji={identity?.emoji || '🌙'} displaySize={28} priority imgClassName="w-7 h-7 rounded-lg object-cover" />
        </div>
        <div className="font-medium text-ink-100" data-testid="chat-header-agent-name">{agentName(identity)}</div>
        {/* 089: the open chat is an ops conversation — say so where the eye
            already is. Amber = attention; same chip anatomy as `debug`. */}
        {activeOps && (
          <span data-testid="ops-tag" className="ml-1 text-[10px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/40 rounded px-1.5 py-0.5">ops</span>
        )}
        {debugMode && (
          <span className="ml-1 text-[10px] font-bold uppercase tracking-wider bg-amber-500/20 text-amber-300 border border-amber-500/30 rounded px-1.5 py-0.5">debug</span>
        )}
      </div>

      {renaming ? (
        <div className="flex items-center gap-1">
          <input
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') doRename(); if (e.key === 'Escape') setRenaming(false) }}
            className="rounded bg-ink-800 px-2 py-1 text-sm text-ink-100 outline-none border border-luna-500/30 w-56"
            placeholder="Conversation title"
          />
          <button onClick={doRename} className="p-1 text-emerald-400 hover:text-emerald-300" title="Save">
            <Check className="w-4 h-4" />
          </button>
          <button onClick={() => setRenaming(false)} className="p-1 text-ink-400 hover:text-ink-200" title="Cancel">
            <X className="w-4 h-4" />
          </button>
        </div>
      ) : (
        activeId && (
          <div className="relative" ref={menuRef}>
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="p-1.5 max-md:p-2.5 rounded-lg hover:bg-white/10 text-ink-400 hover:text-ink-200 transition"
              title="Conversation actions"
              data-testid="chat-header-menu-btn"
            >
              <MoreHorizontal className="w-5 h-5" />
            </button>
            {menuOpen && (
              <>
                <div className="absolute right-0 top-full z-[100] mt-1 w-48 rounded-lg bg-ink-800 py-1 shadow-xl border border-white/10">
                  <button
                    onClick={() => doCopy()}
                    className={cn(
                      'w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/10',
                      copied ? 'text-emerald-400' : 'text-ink-200',
                    )}
                    data-testid="chat-header-copy"
                  >
                    {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    {copied ? 'Copied!' : 'Copy conversation'}
                  </button>
                  <button
                    onClick={() => doCopyContext()}
                    className={cn(
                      'w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-white/10',
                      ctxState === 'copied' ? 'text-emerald-400' : 'text-ink-200',
                    )}
                    data-testid="chat-header-copy-context"
                  >
                    {ctxState === 'copied' ? (
                      <Check className="w-3.5 h-3.5" />
                    ) : ctxState === 'busy' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <FileText className="w-3.5 h-3.5" />
                    )}
                    {ctxState === 'copied' ? 'Copied!' : ctxState === 'busy' ? 'Fetching context…' : 'Copy agent context'}
                  </button>
                  <button
                    onClick={() => { setMenuOpen(false); setDraft(activeTitle || ''); setRenaming(true) }}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sm text-ink-200 hover:bg-white/10"
                    data-testid="chat-header-rename"
                  >
                    <Pencil className="w-3.5 h-3.5" /> Rename
                  </button>
                  {/* 089: ops conversations can't be deleted (the server 403s
                      the DELETE) — no dead affordance, the item is absent. */}
                  {!activeOps && (
                    <button
                      onClick={() => { setMenuOpen(false); doDelete() }}
                      className="w-full flex items-center gap-2 px-3 py-2 text-sm text-rose-400 hover:bg-white/10"
                      data-testid="chat-header-delete"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> Delete
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        )
      )}
    </div>
  )
}

// Conversation sidebar item — just selection now. Rename/delete moved to
// the chat header menu (005.917).
function ConversationItem({
  conv,
  active,
  onSelect,
  big,
}: {
  conv: ConversationSummary & ConvMeta
  active: boolean
  onSelect: () => void
  /** 057: mobile list rows — 44px+ touch targets */
  big?: boolean
}) {
  // 089: ops conversations read amber (title + border) and carry an OPS chip
  // — they're the rows that watch production, pinned above the build chats.
  const ops = convKind(conv) === 'ops'
  return (
    <button
      onClick={onSelect}
      data-testid="conv-item"
      className={cn(
        'w-full text-left px-3 rounded-lg text-sm transition flex items-center gap-1.5 min-w-0',
        big ? 'py-3 min-h-[44px]' : 'py-2',
        active
          ? 'bg-luna-600/20 text-luna-100 border border-luna-500/30'
          : 'text-ink-300 hover:text-ink-50 hover:bg-white/5 border border-transparent',
        ops && 'text-amber-300 border-amber-500/40',
        ops && active && 'bg-amber-500/10',
      )}
      title={conv.title || 'Untitled'}
    >
      <span className="truncate">{conv.title || 'New conversation'}</span>
      {ops && (
        <span
          data-testid="ops-tag"
          className="ml-auto shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/40"
        >
          OPS
        </span>
      )}
    </button>
  )
}

// 005.82-fixes2 item O: interleave approval cards into the message timeline by
// timestamp instead of appending them after every message (which pinned the
// card at the bottom, out of order). Messages tie-break ahead of cards so the
// triggering prose renders before its card.
export function renderTimeline(
  messages: UIMessage[],
  approvals: ApprovalRecord[],
  activeId: string | null,
  emoji: string,
  avatarUrl?: string | null,
  agentName?: string,
  debugEvents?: DebugEvent[] | null,
  secretReqs?: SecretRequestSummary[],
  onSecretResolved?: (id: string, status: 'fulfilled' | 'cancelled') => void,
  planTasks?: PlanTask[],
  planCreatedAt?: string | null,
  // 027: is a turn running? Gates the live card's spinner; when paused the
  // card grows Resume/dismiss owner controls.
  planWorking?: boolean,
  onPlanResume?: () => void,
  onPlanDismiss?: () => void,
  // 038/phase04: a resume nudge is pending — Resume renders disabled as
  // "Resuming…" until work becomes visible.
  planResuming?: boolean,
  // 043: the live card folds to a one-line pill; remembered per plan.
  planCollapsed?: boolean,
  onPlanToggleCollapse?: () => void,
  // 080: error-card Retry (disabled while a turn is streaming).
  onTurnErrorRetry?: (cardId: string) => void,
  turnErrorRetryDisabled?: boolean,
  // 089: auto-approval ids whose receipts the wrench progress chip replaces
  // (tagged live, per turn) — they never render as AutoToolReceipts rows.
  suppressAutoIds?: Set<string>,
) {
  const ts = (s?: string | null) => (s ? Date.parse(s) : 0)
  const visible = messages.filter(
    (m) =>
      m.kind === 'muted' ||
      m.kind === 'task_plan' ||
      // 056: a standalone card row — the embed IS the content.
      (m.kind === 'card' && !!m.embed_iframe) ||
      m.role === 'user' ||
      !!m.notice ||
      !!m.policy_block ||
      m.system_line ||
      // 056: an empty assistant reply with an embed still renders (the embed
      // is the message); empty + no embed + not pending stays suppressed.
      (m.role === 'assistant' &&
        (m.content.trim() !== '' || !!m.pending || !!m.embed_iframe || !!m.embed_html)),
  )
  const groups = groupApprovals(approvals.filter((a) => a.req.conversation_id === activeId))
  // 028.1: auto-approved receipts don't render as one line each — consecutive
  // ones coalesce into a single row of tool chips (see AutoToolReceipts).
  const items: {
    ts: number
    order: number
    node: React.ReactNode
    auto?: { label: string; id: string }
  }[] = []
  visible.forEach((m, i) =>
    items.push({
      ts: ts(m.created_at),
      order: i, // messages before cards on ts ties (prose before its card)
      node: m.kind === 'task_plan'
        ? (
          // 026: a finished plan, anchored forever at its completion point.
          <div key={`tp-${m.id}`} className="flex gap-3 fade-in justify-start">
            <div className="w-8 shrink-0" />
            <TaskPlanCard
              tasks={m.tasks ?? []}
              intent={m.title || undefined}
              variant={
                m.plan_status === 'superseded' || m.plan_status === 'dismissed'
                  ? m.plan_status
                  : 'completed'
              }
            />
          </div>
        )
        // 056: a standalone plugin card — full message-column width, no
        // avatar, no bubble chrome; the sandboxed embed is the whole row.
        : m.kind === 'card'
        ? (
          <div key={`cd-${m.id}`} data-testid="card-row" className="flex gap-3 fade-in justify-start">
            <div className="w-8 shrink-0" />
            <div className="flex-1 min-w-0">
              <PluginEmbed
                html={m.embed_iframe || ''}
                asIframe
                card
                source={m.source}
                conversationId={activeId ?? undefined}
                messageId={m.id}
              />
            </div>
          </div>
        )
        : m.kind === 'muted'
        ? <MutedLine key={`mu-${m.id}`} title={m.title || 'System update'} content={m.content} />
        // 028: a turn that died after tool activity without a closing message —
        // render the persisted marker as a muted status line, not an agent bubble.
        : m.kind === 'turn_interrupted'
        ? <SystemLine key={`ti-${m.id}`} text={m.content} />
        // 036/phase02: ⏹ a turn broken by an urgent message or a stop.
        : m.kind === 'turn_stopped'
        ? <SystemLine key={`ts-${m.id}`} text={m.content} />
        // 036/phase02: a draft cut off mid-delivery — preserved, collapsed,
        // clearly labeled as never delivered.
        : m.kind === 'superseded_partial'
        ? <InterruptedDraft key={`ip-${m.id}`} content={m.content} />
        // 039: billing gateway refused the turn — persisted marker (kind) or
        // live SSE event (policy_block) both render the action-required banner.
        // 080: the turn failed — honest card (persisted kind or live event),
        // Retry resends the preceding user message once the cause has passed.
        : m.kind === 'turn_error'
        ? <TurnErrorCard key={`te-${m.id}`} message={m} disabled={turnErrorRetryDisabled} onRetry={() => onTurnErrorRetry?.(m.id)} />
        : m.kind === 'policy_blocked'
        ? <PolicyBlockedBanner key={`pb-${m.id}`} block={{ message: m.content.replace(/^⚠ /, '') }} />
        : m.policy_block
        ? <PolicyBlockedBanner key={`pb-${m.id}`} block={m.policy_block} />
        : m.system_line
          ? <SystemLine key={`sl-${m.id}`} text={m.content} />
          : m.notice
            ? <FallbackNotice key={`n-${m.id}`} notice={m.notice} />
            : <MemoBubble key={`m-${m.id}`} message={m} emoji={emoji} avatarUrl={avatarUrl} />,
    }),
  )
  // 026: the LIVE plan card — inline at the plan's creation time, a normal
  // timeline row that scrolls with the chat (it lives only in the plan's home
  // conversation; when the plan completes an anchored kind==="task_plan"
  // message replaces it and this card goes away).
  if (planTasks && planTasks.length > 0) {
    items.push({
      ts: planCreatedAt ? ts(planCreatedAt) : Number.MAX_SAFE_INTEGER,
      order: 3_000_000,
      node: (
        <div key="task-plan-live" className="flex gap-3 fade-in justify-start">
          <div className="w-8 shrink-0" />
          <TaskPlanCard
            tasks={planTasks}
            working={planWorking}
            resuming={planResuming}
            collapsed={planCollapsed}
            onToggleCollapse={onPlanToggleCollapse}
            onResume={onPlanResume}
            onDismiss={onPlanDismiss}
          />
        </div>
      ),
    })
  }
  groups.forEach((g, i) => {
    // 005.907: use latest pending member's timestamp so new turns produce
    // correctly-anchored cards; React key from member ids so new pending
    // groups always produce new DOM nodes (no morphing across turns).
    // 010.5: decided groups are singletons now — anchor each at its own
    // request time so it lands inline where the action happened (no jump to a
    // batch's latest member).
    const groupTs = Math.max(...g.reqs.map((r) => ts(r.requested_at)))
    const groupKey = `a-${g.reqs.map((r) => r.id).join('-')}`
    // 028.1: an auto-approved receipt (owner never prompted) is not a card —
    // it becomes a compact tool chip, coalesced with its neighbours below.
    if (g.decided && isAutoApproved(g.decided)) {
      // 089: this receipt happened during a live turn this tab painted — the
      // wrench chip already accounts for it; no timeline row, then or later.
      if (suppressAutoIds?.has(g.reqs[0].id)) return
      const tool = (g.reqs[0].payload as { tool?: string })?.tool
      items.push({
        ts: groupTs,
        order: 1_000_000 + i,
        auto: { label: tool ? humanizeTool(tool) : g.reqs[0].summary, id: g.reqs[0].id },
        node: null,
      })
      return
    }
    items.push({
      ts: groupTs,
      order: 1_000_000 + i,
      node: (
        <div key={groupKey} className="flex gap-3 fade-in justify-start">
          <div className="w-8 shrink-0" />
          <InlineApprovalCard reqs={g.reqs} decided={g.decided} agentName={agentName} />
        </div>
      ),
    })
  })
  // 006.708: interleave secret-form cards by created_at, same as approvals.
  if (secretReqs) {
    secretReqs
      .filter((r) => r.conversation_id === activeId)
      .forEach((r, i) => {
        items.push({
          ts: ts(r.created_at),
          order: 2_000_000 + i,
          node: (
            <div key={`s-${r.id}`} className="flex gap-3 fade-in justify-start">
              <div className="w-8 shrink-0" />
              <InlineSecretForm
                req={r}
                onResolved={(status) => onSecretResolved?.(r.id, status)}
              />
            </div>
          ),
        })
      })
  }
  // 005.913: when debug mode is on, fold each captured bus event into the
  // timeline as a flat row (no bubble, no avatar). They're ordered by their
  // server-side ts so a tool.called → tool.completed pair shows in order
  // around the assistant prose that triggered it.
  if (debugEvents && debugEvents.length > 0) {
    debugEvents.forEach((e, i) => {
      items.push({
        ts: e.ts * 1000, // event ts is unix seconds; bubble ts is millis
        order: 500_000 + i,
        node: <DebugRow key={`d-${i}-${e.event}-${e.ts}`} event={e} />,
      })
    })
  }
  items.sort((a, b) => a.ts - b.ts || a.order - b.order)
  // 028.1: coalesce consecutive auto-approved receipts into one chip row —
  // a turn that fires 15 auto tools shows one compact line, not 15.
  const out: React.ReactNode[] = []
  let run: { label: string; id: string }[] = []
  const flushRun = () => {
    if (run.length === 0) return
    out.push(<AutoToolReceipts key={`auto-${run[0].id}`} calls={run} />)
    run = []
  }
  for (const it of items) {
    if (it.auto) {
      run.push(it.auto)
      continue
    }
    flushRun()
    out.push(it.node)
  }
  flushRun()
  return out
}

// 028.1: compact receipts for silently auto-approved tool calls. One flat
// wrap-row of chips; repeated calls of the same tool collapse into numbered
// tags ("Scope set #2 #3") instead of one line per call. No approval wording,
// no link — these never asked the owner anything.
function AutoToolReceipts({ calls }: { calls: { label: string; id: string }[] }) {
  const clusters: { label: string; ids: string[] }[] = []
  for (const c of calls) {
    const last = clusters[clusters.length - 1]
    if (last && last.label === c.label) last.ids.push(c.id)
    else clusters.push({ label: c.label, ids: [c.id] })
  }
  return (
    <div
      data-testid="auto-tool-receipts"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-11 text-[11px] leading-5 text-ink-500 fade-in"
    >
      {clusters.map((cl) => (
        <span key={cl.ids[0]} className="inline-flex items-center gap-1">
          <Wrench className="h-3 w-3 shrink-0" />
          <span>{cl.label}</span>
          {cl.ids.slice(1).map((id, i) => (
            <span key={id} className="text-ink-600">
              #{i + 2}
            </span>
          ))}
        </span>
      ))}
    </div>
  )
}

// 007.016: inline, always-visible "the model degraded" notice. A fallback is
// automatic (no approval) but the user must know it happened.
function prettyModel(s?: string): string {
  if (!s) return 'a fallback model'
  return s.includes(':') ? s.split(':').slice(1).join(':') : s
}
function FallbackNotice({ notice }: { notice: { from?: string; to?: string; reason?: string } }) {
  return (
    <div className="flex justify-center fade-in my-1">
      <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-[12px] text-amber-300">
        <span>⚠</span>
        <span>
          {prettyModel(notice.from)} unavailable
          {notice.reason ? ` (${notice.reason})` : ''} — switched to {prettyModel(notice.to)} for this reply
        </span>
      </div>
    </div>
  )
}

// 039: the billing gateway refused the turn (402) — unlike a fallback notice
// this is a STOP: no reply was produced and the owner must act (top up, wait
// for a limit window, or fix billing). Rendered as a centered banner.
function PolicyBlockedBanner({ block }: { block: { code?: string; message?: string; retryable?: boolean } }) {
  return (
    <div data-testid="policy-blocked-banner" className="flex justify-center fade-in my-2">
      <div className="max-w-md rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2.5 text-[13px] text-red-300">
        <div className="flex items-center gap-2 font-medium">
          <span>⛔</span>
          <span>Message not processed</span>
        </div>
        <div className="mt-1 text-red-200/90">
          {block.message || 'Blocked by billing policy.'}
          {block.retryable ? ' You can try again later.' : ''}
        </div>
      </div>
    </div>
  )
}

// 080: a turn that failed with a typed notice. Unlike a fallback notice this
// is a STOP for the turn; unlike the billing banner it is usually transient
// (storage refused connections, provider hiccup) — so it offers Retry, which
// resends the message that preceded it. The headline is plain language; the
// code is shown small for support; the raw exception never reaches the UI.
const TURN_ERROR_LABEL: Record<string, string> = {
  storage_unavailable: 'Storage unavailable',
  context_overflow: 'Context limit reached',
  history_corrupt: 'History problem',
  model_error: 'Model error',
  internal: 'Something went wrong',
}
function TurnErrorCard({ message, disabled, onRetry }: { message: UIMessage; disabled?: boolean; onRetry: () => void }) {
  const code = message.error_code || 'internal'
  const retryable = message.retryable !== false
  return (
    <div data-testid="turn-error-card" data-error-code={code} className="flex justify-center fade-in my-2">
      <div className="max-w-md w-full rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 text-[13px] text-amber-200">
        <div className="flex items-center gap-2 font-medium">
          <span>⚠</span>
          <span>{TURN_ERROR_LABEL[code] || TURN_ERROR_LABEL.internal}</span>
          <span className="ml-auto text-[10px] uppercase tracking-wide text-amber-200/50">{code}</span>
        </div>
        <div className="mt-1 text-amber-100/90">{message.content}</div>
        {retryable && (
          <div className="mt-2 flex justify-end">
            <button
              type="button"
              data-testid="turn-error-retry"
              disabled={disabled}
              onClick={onRetry}
              className="rounded-md border border-amber-400/40 bg-amber-400/10 px-3 py-1 text-[12px] font-medium text-amber-100 hover:bg-amber-400/20 disabled:opacity-50 transition-colors"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

// 005.913: inline debug-event row. No avatar, no bubble — just one flat
// monospace line. Hidden unless debug mode is on (handled by the caller
// deciding whether to pass debug events).
// 007.013-E: condensed to a single non-wrapping line at line-height 1.5 with
// minimal vertical padding. Click to expand the full multi-line detail.
function DebugRow({ event }: { event: DebugEvent }) {
  const [expanded, setExpanded] = useState(false)
  const fullLines = formatDebugLine(event).split('\n')
  const hasDetail = fullLines.length > 1
  return (
    <div
      data-testid="debug-row"
      onClick={() => hasDetail && setExpanded((v) => !v)}
      className={cn(
        'px-2 py-px font-mono text-[11px] leading-[1.5] text-ink-500 select-text',
        hasDetail && 'cursor-pointer hover:text-ink-400',
        expanded ? 'whitespace-pre-wrap' : 'truncate',
      )}
    >
      {expanded
        ? fullLines.map((l, i) => <div key={i}>{l}</div>)
        : formatDebugLineCompact(event)}
    </div>
  )
}

const KNOWN_ROLES: UIMessage['role'][] = ['user', 'assistant', 'tool', 'system']

function apiToUI(m: ApiMessage): UIMessage {
  // 005.82-fixes2 item G: normalize unknown/extra roles (e.g. "summary")
  // to a known value instead of an unchecked cast that lies to the type.
  const role = (KNOWN_ROLES as string[]).includes(m.role)
    ? (m.role as UIMessage['role'])
    : 'system'
  return {
    id: m.id,
    role,
    content: m.content,
    created_at: m.created_at,
    embed_iframe: m.embed_iframe ?? undefined,
    embed_html: m.embed_html ?? undefined,
    source: m.source ?? undefined,
    kind: m.kind ?? undefined,
    title: m.title ?? undefined,
    plan_status: m.plan_status ?? undefined,
    tasks: m.tasks ?? undefined,
    attachments: m.attachments ?? undefined,
    reasoning: m.reasoning ?? undefined,
    reasoning_ms: m.reasoning_ms ?? undefined,
    error_code: m.error_code ?? undefined,
    retryable: m.retryable ?? undefined,
  }
}

// Hides the trash icon when unused (placeholder for later)
export { Trash2 }
