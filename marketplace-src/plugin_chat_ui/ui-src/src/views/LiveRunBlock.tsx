// 078 (chat-ui 0.11): live tool-run block. A plugin that emits the generic
// `ui.live.*` protocol on the event bus DURING its tool call gets a block in
// the pending assistant bubble that shows the code the moment the run starts,
// streams stdout/stderr as it happens, then folds to a one-line pill — like
// the reasoning trace, and unlike the old iframe card that appeared only after
// the fact. Protocol (any tool may speak it; today plugin-inline-code-run):
//
//   ui.live.open   {id, tool, lang?, title?, code}
//   ui.live.append {id, stream: "stdout"|"stderr", text}
//   ui.live.close  {id, status: "success"|"error"|"timeout"|"refused",
//                   exit_code?, duration_ms?, files?, note?}
//
// The persisted tool result still carries the plugin's `embed_iframe` card for
// reloads; while a message holds live runs the bubble shows THESE instead.
import { memo, useEffect, useRef, useState } from 'react'
import { ChevronDown, Loader2 } from 'lucide-react'
import { cn } from '@luna/lib/cn'

export type LiveRunStatus = 'running' | 'success' | 'error' | 'timeout' | 'refused'

export interface LiveRun {
  id: string
  tool: string
  lang: string
  title?: string
  code: string
  stdout: string
  stderr: string
  status: LiveRunStatus
  started_at: number
  duration_ms?: number
  exit_code?: number | null
  files?: number
  note?: string
}

export type LiveEvent = { type: string; [key: string]: unknown }

export function isLiveEvent(evt: { type?: string } | null | undefined): boolean {
  return typeof evt?.type === 'string' && evt.type.startsWith('ui.live.')
}

// Ceiling on live text kept per stream — the plugin caps at 64K anyway; this
// is the belt to its braces so a rogue emitter can't balloon React state.
const STREAM_CAP = 96_000

/** Pure reducer: apply one ui.live.* frame to a message's run list. Unknown
 *  ids on append/close are ignored (a frame from a run this tab never saw). */
export function applyLiveEvent(runs: LiveRun[] | undefined, evt: LiveEvent, now: number): LiveRun[] {
  const list = runs ?? []
  const id = typeof evt.id === 'string' ? evt.id : ''
  if (!id) return list
  if (evt.type === 'ui.live.open') {
    const run: LiveRun = {
      id,
      tool: typeof evt.tool === 'string' && evt.tool ? evt.tool : 'tool',
      lang: typeof evt.lang === 'string' && evt.lang ? evt.lang : '',
      title: typeof evt.title === 'string' && evt.title.trim() ? evt.title.trim() : undefined,
      code: typeof evt.code === 'string' ? evt.code : '',
      stdout: '',
      stderr: '',
      status: 'running',
      started_at: now,
    }
    const idx = list.findIndex((r) => r.id === id)
    if (idx >= 0) {
      const copy = [...list]
      copy[idx] = run
      return copy
    }
    return [...list, run]
  }
  const idx = list.findIndex((r) => r.id === id)
  if (idx < 0) return list
  const cur = list[idx]
  if (evt.type === 'ui.live.append') {
    const text = typeof evt.text === 'string' ? evt.text : ''
    if (!text) return list
    const key = evt.stream === 'stderr' ? 'stderr' : 'stdout'
    let next = cur[key] + text
    if (next.length > STREAM_CAP) next = next.slice(next.length - STREAM_CAP)
    const copy = [...list]
    copy[idx] = { ...cur, [key]: next }
    return copy
  }
  if (evt.type === 'ui.live.close') {
    const s = evt.status
    const status: LiveRunStatus =
      s === 'success' || s === 'timeout' || s === 'refused' || s === 'error' ? s : 'error'
    const copy = [...list]
    copy[idx] = {
      ...cur,
      status,
      duration_ms: typeof evt.duration_ms === 'number' ? evt.duration_ms : now - cur.started_at,
      exit_code: typeof evt.exit_code === 'number' ? evt.exit_code : null,
      files: typeof evt.files === 'number' ? evt.files : 0,
      note: typeof evt.note === 'string' && evt.note ? evt.note : undefined,
    }
    return copy
  }
  return list
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`
  return `${(ms / 1000).toFixed(1)} s`
}

const STATUS_LABEL: Record<Exclude<LiveRunStatus, 'running'>, string> = {
  success: 'success',
  error: 'failed',
  timeout: 'timed out',
  refused: 'refused',
}

// Line-by-line reveal of the code when the block opens: the code IS complete
// when the run starts (the model wrote it before the tool ran), so this is a
// short beat that says "this is being run now", capped so a long script never
// stalls the view. Honors prefers-reduced-motion.
const REVEAL_TOTAL_MS = 650
const REVEAL_MAX_STEP_MS = 40

function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false)
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    setReduce(mq.matches)
    const on = () => setReduce(mq.matches)
    mq.addEventListener?.('change', on)
    return () => mq.removeEventListener?.('change', on)
  }, [])
  return reduce
}

const StatusBadge = memo(function StatusBadge({ status }: { status: LiveRunStatus }) {
  if (status === 'running') {
    return (
      <span
        className="grid place-items-center w-5 h-5 rounded-md bg-luna-500/15 text-luna-300 shrink-0"
        data-testid="live-run-spinner"
      >
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
      </span>
    )
  }
  const cls =
    status === 'success'
      ? 'bg-emerald-500/20 text-emerald-300'
      : status === 'timeout' || status === 'refused'
        ? 'bg-amber-500/20 text-amber-300'
        : 'bg-red-500/20 text-red-300'
  const glyph = status === 'success' ? '✓' : status === 'timeout' ? '⏱' : status === 'refused' ? '⚠' : '✗'
  return (
    <span
      className={cn('grid place-items-center w-5 h-5 rounded-md text-[12px] font-bold shrink-0', cls)}
      data-testid="live-run-badge"
      data-status={status}
    >
      {glyph}
    </span>
  )
})

export const LiveRunBlock = memo(function LiveRunBlock({ run }: { run: LiveRun }) {
  const running = run.status === 'running'
  const reduce = useReducedMotion()
  // Open while running; auto-fold shortly after close (unless the user has
  // taken control of the toggle) — the same beat as the reasoning trace.
  const [open, setOpen] = useState(true)
  const userToggledRef = useRef(false)
  useEffect(() => {
    if (running || userToggledRef.current) return
    const t = window.setTimeout(() => setOpen(false), reduce ? 0 : 1200)
    return () => window.clearTimeout(t)
  }, [running, reduce])

  // Elapsed clock while running.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!running) return
    const t = window.setInterval(() => setTick((n) => n + 1), 250)
    return () => window.clearInterval(t)
  }, [running])
  void tick
  const elapsedMs = running ? Date.now() - run.started_at : (run.duration_ms ?? 0)

  // Code reveal.
  const lines = run.code.split('\n')
  const [shown, setShown] = useState(reduce ? lines.length : 0)
  useEffect(() => {
    if (reduce || shown >= lines.length) return
    const step = Math.min(REVEAL_MAX_STEP_MS, REVEAL_TOTAL_MS / Math.max(1, lines.length))
    const t = window.setTimeout(() => setShown((n) => Math.min(lines.length, n + 1)), step)
    return () => window.clearTimeout(t)
  }, [shown, lines.length, reduce])
  const codeShown = reduce ? run.code : lines.slice(0, shown).join('\n')
  const revealing = !reduce && shown < lines.length

  // Output pane pinned to its newest line while streaming.
  const outRef = useRef<HTMLPreElement | null>(null)
  useEffect(() => {
    if (running && outRef.current) outRef.current.scrollTop = outRef.current.scrollHeight
  }, [run.stdout, running])
  const codeRef = useRef<HTMLPreElement | null>(null)
  useEffect(() => {
    if (revealing && codeRef.current) codeRef.current.scrollTop = codeRef.current.scrollHeight
  }, [shown, revealing])

  const filesTxt =
    !running && typeof run.files === 'number' ? `${run.files} file${run.files === 1 ? '' : 's'}` : null
  const headline = running ? 'Running' : STATUS_LABEL[run.status]

  return (
    <div
      className="mt-2 rounded-xl border border-white/10 bg-black/30 overflow-hidden text-[13px]"
      data-testid="live-run"
      data-status={run.status}
    >
      <button
        type="button"
        onClick={() => {
          userToggledRef.current = true
          setOpen((v) => !v)
        }}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-white/[.03] transition"
        data-testid="live-run-head"
      >
        <StatusBadge status={run.status} />
        <span className="font-semibold text-ink-200 shrink-0">
          <span className="font-mono">{run.tool}</span>
          {run.lang && <span className="text-ink-500 font-normal"> · {run.lang}</span>}
        </span>
        {run.title && (
          <span className="text-ink-400 truncate min-w-0" data-testid="live-run-title">
            {run.title}
          </span>
        )}
        <span className="ml-auto flex items-center gap-2.5 text-[12px] text-ink-500 tabular-nums shrink-0">
          <span className={cn(running && 'text-luna-300')}>{headline}</span>
          <span data-testid="live-run-elapsed">{fmtDuration(elapsedMs)}</span>
          {filesTxt && <span>{filesTxt}</span>}
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !open && '-rotate-90')} />
        </span>
      </button>
      {open && (
        <div className="border-t border-white/10" data-testid="live-run-body">
          {run.note && (
            <div className="px-3 py-1.5 text-[12px] text-amber-300/90 border-b border-white/10">{run.note}</div>
          )}
          <div className="px-3 pt-2 pb-2">
            <div className="text-[10.5px] uppercase tracking-[0.16em] text-ink-500 font-semibold mb-1">Code</div>
            <pre
              ref={codeRef}
              data-testid="live-run-code"
              className="m-0 p-2.5 rounded-lg bg-black/40 font-mono text-[12px] leading-relaxed text-ink-200 overflow-auto max-h-56 whitespace-pre"
            >
              {codeShown}
              {revealing && <span className="text-luna-300">▍</span>}
            </pre>
          </div>
          <div className="px-3 pb-2">
            <div className="text-[10.5px] uppercase tracking-[0.16em] text-ink-500 font-semibold mb-1">Output</div>
            <pre
              ref={outRef}
              data-testid="live-run-stdout"
              className="m-0 p-2.5 rounded-lg bg-black/40 font-mono text-[12px] leading-relaxed text-ink-200 overflow-auto max-h-40 whitespace-pre-wrap break-words"
            >
              {run.stdout || (
                <span className={cn('text-ink-500', running && 'dots')}>{running ? 'waiting for output' : '(no output)'}</span>
              )}
            </pre>
          </div>
          {run.stderr && (
            <div className="px-3 pb-2">
              <div className="text-[10.5px] uppercase tracking-[0.16em] text-red-400/80 font-semibold mb-1">Errors</div>
              <pre
                data-testid="live-run-stderr"
                className="m-0 p-2.5 rounded-lg bg-black/40 font-mono text-[12px] leading-relaxed text-red-300 overflow-auto max-h-40 whitespace-pre-wrap break-words"
              >
                {run.stderr}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
})

export const LiveRunList = memo(function LiveRunList({ runs }: { runs: LiveRun[] }) {
  return (
    <div data-testid="live-runs">
      {runs.map((r) => (
        <LiveRunBlock key={r.id} run={r} />
      ))}
    </div>
  )
})
