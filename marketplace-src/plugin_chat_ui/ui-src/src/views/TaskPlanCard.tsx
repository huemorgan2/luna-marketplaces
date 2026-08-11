/**
 * Agent task plan — a compact Cursor-style checklist card.
 *
 * Two lives (026):
 *  - ACTIVE: rendered inline in the timeline at the plan's creation point and
 *    made sticky by the caller so it lingers at the top of the chat while the
 *    agent works. The backend streams the FULL current plan on every
 *    `ui.tasks` frame; this component just renders the latest snapshot.
 *  - ANCHORED: a finished plan persisted as a `kind==="task_plan"` message —
 *    a frozen snapshot rendered at its permanent place in the timeline
 *    (variant "completed", "superseded" or "dismissed").
 *
 * 027 — honesty + owner controls on the ACTIVE card:
 *  - the in_progress spinner animates only while a turn is actually running
 *    (`working`); between turns the server demotes in_progress anyway, so a
 *    paused plan shows plain circles, never a phantom loader.
 *  - when paused, the owner gets Resume (nudge the agent to continue — the
 *    primary action) and a quiet ✕ (dismiss a plan that is simply obsolete).
 */

import { Circle, CheckCircle2, ChevronDown, Clock, Loader2, AlertTriangle, Minus, OctagonAlert, XCircle, ListTodo, Play, X } from 'lucide-react'
import { cn } from '@luna/lib/cn'
import type { PlanTask, PlanSnapshotTask } from '@luna/lib/api'

// Active plans carry full PlanTask rows; anchored snapshots have no id/intent.
type CardTask = PlanTask | PlanSnapshotTask

function StatusGlyph({ status, working }: { status: PlanTask['status']; working: boolean }) {
  switch (status) {
    case 'done':
      return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5 text-emerald-400" />
    case 'in_progress':
      // 027: spin only while a turn is running — a static half-ring otherwise.
      return (
        <Loader2
          className={cn('h-3.5 w-3.5 shrink-0 mt-0.5 text-luna-300', working && 'animate-spin')}
        />
      )
    case 'blocked':
      return <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-amber-400" />
    case 'stalled':
      // 075/phase7: parked with a verdict — visually louder than blocked.
      return <OctagonAlert className="h-3.5 w-3.5 shrink-0 mt-0.5 text-red-400" />
    case 'expired':
      // 075/phase7: TTL-swept — the plan aged out before finishing.
      return <Clock className="h-3.5 w-3.5 shrink-0 mt-0.5 text-ink-500" />
    case 'cancelled':
      return <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-ink-500" />
    default: // open
      return <Circle className="h-3.5 w-3.5 shrink-0 mt-0.5 text-ink-500" />
  }
}

// 075/phase7: one honest line about WHY nothing is happening. "Paused" is
// reserved for "the owner paused it / nothing is scheduled" — a waiting wake,
// an ask on the owner, a stalled verdict and an expired plan each say so.
function pausedStateLine(sorted: CardTask[]): string {
  const blocked = sorted.find((t) => t.status === 'blocked')
  if (blocked) {
    const wake = 'wake_at' in blocked ? blocked.wake_at || '' : ''
    if (wake.startsWith('event:')) return `waiting — resumes when '${wake.slice(6)}' fires`
    if (wake) {
      const d = new Date(wake)
      if (!isNaN(d.getTime())) {
        const hm = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        return `waiting — wakes ${hm}`
      }
    }
    return blocked.blocked_reason
      ? `blocked on you: ${blocked.blocked_reason}`
      : 'blocked — the agent is waiting on you'
  }
  const stalled = sorted.find((t) => t.status === 'stalled')
  if (stalled) {
    const verdict = stalled.blocked_reason || ''
    // Watchdog cap-park verdicts already start with "stalled:" — don't stutter.
    if (!verdict) return 'stalled'
    return verdict.startsWith('stalled') ? verdict : `stalled: ${verdict}`
  }
  if (sorted.some((t) => t.status === 'expired')) {
    return 'expired — this plan aged out before finishing'
  }
  return 'paused — the agent isn’t working on this right now'
}

// 075/phase7: attempt-ledger badge (HEAD row) — burning retries without
// progress and ceiling-cut resumes are owner-visible, not silent.
function ledgerBadge(head: CardTask | undefined): string | null {
  if (!head || !('attempts' in head)) return null
  const attempts = head.attempts ?? 0
  const outcome = head.last_attempt_outcome ?? ''
  if (outcome === 'aborted') return 'last resume hit its usage ceiling — resumable'
  if (outcome === 'no_progress' && attempts >= 2) return `${attempts} resume attempts without progress`
  return null
}

const MUTED_LABELS: Record<string, string> = {
  superseded: 'superseded',
  dismissed: 'dismissed',
}

export function TaskPlanCard({
  tasks,
  intent,
  variant = 'active',
  working = false,
  resuming = false,
  collapsed = false,
  onToggleCollapse,
  onResume,
  onDismiss,
  className,
}: {
  tasks: CardTask[]
  intent?: string
  variant?: 'active' | 'completed' | 'superseded' | 'dismissed'
  /** 027: is a turn running right now? Gates the spinner and hides the owner buttons. */
  working?: boolean
  /** 038/phase04: a resume nudge is pending — Resume shows disabled as "Resuming…". */
  resuming?: boolean
  /** 043: the active card can fold to a one-line pill that stays in place. */
  collapsed?: boolean
  onToggleCollapse?: () => void
  onResume?: () => void
  onDismiss?: () => void
  className?: string
}) {
  if (tasks.length === 0) return null
  const sorted = [...tasks].sort((a, b) => a.sort_order - b.sort_order)
  const doneCount = sorted.filter((t) => t.status === 'done').length
  const allDone = doneCount === sorted.filter((t) => t.status !== 'cancelled').length
  const muted = variant === 'superseded' || variant === 'dismissed'
  const green = !muted && (variant === 'completed' || allDone)
  const heading = intent ?? ('intent' in sorted[0] ? sorted[0].intent : '')
  const paused = variant === 'active' && !working
  // 043: collapsed pill — title + progress, expand on click. Active card only.
  if (collapsed && variant === 'active') {
    return (
      <button
        data-testid="task-plan-pill"
        data-working={working}
        onClick={onToggleCollapse}
        title="Expand the plan"
        className={cn(
          'mt-2 inline-flex max-w-md items-center gap-2 rounded-full border border-white/10',
          'bg-ink-900/60 px-3 py-1.5 text-left hover:border-white/20',
          className,
        )}
      >
        {working ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-luna-300" />
        ) : (
          <ListTodo className="h-3.5 w-3.5 shrink-0 text-luna-300" />
        )}
        <span className="min-w-0 truncate text-xs font-medium text-ink-100">{heading}</span>
        <span className="shrink-0 text-[11px] text-ink-400 tabular-nums">
          {doneCount}/{sorted.length}
        </span>
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-ink-500" />
      </button>
    )
  }
  return (
    <div
      data-testid="task-plan-card"
      data-variant={variant}
      data-working={variant === 'active' ? working : undefined}
      className={cn(
        'mt-2 max-w-md rounded-xl border p-3.5',
        green ? 'border-emerald-500/30 bg-emerald-950/20' : 'border-white/10 bg-ink-900/60',
        muted && 'opacity-70',
        className,
      )}
    >
      <div className="flex items-center gap-2 mb-2">
        <ListTodo className={cn('h-4 w-4', green ? 'text-emerald-400' : muted ? 'text-ink-500' : 'text-luna-300')} />
        <span className="text-sm font-medium text-ink-100 flex-1 truncate">{heading}</span>
        {muted && <span className="text-[11px] text-ink-500">{MUTED_LABELS[variant]}</span>}
        <span className="text-[11px] text-ink-400 tabular-nums">
          {doneCount}/{sorted.length}
        </span>
        {variant === 'active' && onToggleCollapse && (
          <button
            data-testid="task-plan-collapse"
            onClick={onToggleCollapse}
            title="Minimize the plan"
            className="p-0.5 rounded text-ink-500 hover:text-ink-200 hover:bg-white/10"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
        )}
        {paused && onDismiss && (
          <button
            data-testid="task-plan-dismiss"
            onClick={onDismiss}
            title="Dismiss this plan (the agent is told it was closed)"
            className="p-0.5 rounded text-ink-500 hover:text-ink-200 hover:bg-white/10"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <ul className="space-y-1.5">
        {sorted.map((t, i) => (
          <li
            key={'id' in t ? t.id : i}
            data-testid="task-plan-item"
            data-status={t.status}
            className="flex items-start gap-2"
          >
            <StatusGlyph status={t.status} working={working} />
            <div className="min-w-0">
              <div
                className={cn(
                  'text-xs leading-[1.4]',
                  t.status === 'done' ? 'text-ink-400 line-through' : 'text-ink-200',
                )}
              >
                {t.title}
              </div>
              {(t.status === 'blocked' || t.status === 'stalled') && t.blocked_reason && (
                // 075/phase7: blocked shows the ask; stalled shows the verdict.
                <div className="text-[11px] text-ink-500">{t.blocked_reason}</div>
              )}
            </div>
          </li>
        ))}
      </ul>
      {variant === 'active' && !allDone && (() => {
        // 075/phase7: honest state surfaces — resume note + ledger badge show
        // whenever the plan is live; the state line replaces blanket "paused".
        const head = sorted[0]
        const note = head && 'resume_note' in head ? head.resume_note || '' : ''
        const badge = ledgerBadge(head)
        return (
          <>
            {note && (
              <div data-testid="task-plan-note" className="mt-2 text-[11px] text-ink-400">
                <span className="text-ink-500">Next action:</span> {note}
              </div>
            )}
            {badge && (
              <div
                data-testid="task-plan-ledger"
                className="mt-1.5 inline-flex rounded-md bg-amber-500/10 px-1.5 py-0.5 text-[11px] text-amber-300/90"
              >
                {badge}
              </div>
            )}
          </>
        )
      })()}
      {paused && onResume && (
        <div className="mt-2.5 pt-2.5 border-t border-white/5 flex items-center gap-2">
          <button
            data-testid="task-plan-resume"
            data-resuming={resuming || undefined}
            onClick={onResume}
            disabled={resuming}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg bg-luna-500/15 text-luna-200 text-[11px] font-medium px-2.5 py-1',
              resuming ? 'opacity-60 cursor-default' : 'hover:bg-luna-500/25',
            )}
          >
            {resuming ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
            {resuming ? 'Resuming…' : 'Resume'}
          </button>
          <span data-testid="task-plan-state" className="text-[11px] text-ink-500">
            {resuming ? 'nudging the agent to continue…' : pausedStateLine(sorted)}
          </span>
        </div>
      )}
    </div>
  )
}
