// @vitest-environment jsdom
// 075/phase7 — honest card states. The owner glancing at the card always
// knows WHY nothing is happening: a waiting wake shows its time (or event),
// a blocked task shows the ask, a stalled task shows the verdict, an expired
// plan says so, and "paused" is reserved for plain owner-paused idleness.
// The resume note renders as "Next action: …" and the attempt ledger badges
// no-progress burns and ceiling-cut (resumable) resumes.
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { TaskPlanCard } from '../views/TaskPlanCard'
import type { PlanTask } from '@luna/lib/api'

afterEach(cleanup)

let n = 0
function task(over: Partial<PlanTask>): PlanTask {
  n += 1
  return {
    id: `t${n}`,
    intent: 'test plan',
    title: `step ${n}`,
    status: 'open',
    blocked_reason: null,
    sort_order: n,
    resume_note: '',
    wake_at: '',
    attempts: 0,
    last_attempt_outcome: '',
    ...over,
  }
}

function renderCard(tasks: PlanTask[], props: Record<string, unknown> = {}) {
  return render(
    <TaskPlanCard
      tasks={tasks}
      working={false}
      onResume={() => {}}
      onDismiss={() => {}}
      {...props}
    />,
  )
}

describe('075/phase7 honest card states', () => {
  it('timed wake renders "waiting — wakes HH:MM"', () => {
    const at = new Date(Date.now() + 30 * 60_000)
    renderCard([
      task({ status: 'done' }),
      task({ status: 'blocked', blocked_reason: 'rate limited', wake_at: at.toISOString() }),
    ])
    const state = screen.getByTestId('task-plan-state')
    const hm = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    expect(state.textContent).toBe(`waiting — wakes ${hm}`)
  })

  it('event wake renders the event name', () => {
    renderCard([
      task({ status: 'blocked', blocked_reason: 'needs plugin', wake_at: 'event:plugin.enabled' }),
    ])
    expect(screen.getByTestId('task-plan-state').textContent).toBe(
      "waiting — resumes when 'plugin.enabled' fires",
    )
  })

  it('blocked without a wake renders "blocked on you: <ask>"', () => {
    renderCard([
      task({ status: 'blocked', blocked_reason: 'grant the Notion key or say skip' }),
    ])
    expect(screen.getByTestId('task-plan-state').textContent).toBe(
      'blocked on you: grant the Notion key or say skip',
    )
  })

  it('stalled renders the verdict on the state line AND under the task row', () => {
    renderCard([
      task({ status: 'stalled', blocked_reason: 'stalled: 5 attempts without progress' }),
    ])
    // watchdog verdicts already start with "stalled:" — no stutter
    expect(screen.getByTestId('task-plan-state').textContent).toBe(
      'stalled: 5 attempts without progress',
    )
    const item = screen.getByTestId('task-plan-item')
    expect(item.getAttribute('data-status')).toBe('stalled')
    expect(item.textContent).toContain('5 attempts without progress')
  })

  it('expired renders the aged-out line', () => {
    renderCard([task({ status: 'expired' })])
    expect(screen.getByTestId('task-plan-state').textContent).toBe(
      'expired — this plan aged out before finishing',
    )
  })

  it('plain idle plan still says paused', () => {
    renderCard([task({ status: 'open' })])
    expect(screen.getByTestId('task-plan-state').textContent).toBe(
      'paused — the agent isn’t working on this right now',
    )
  })

  it('working card shows no state footer (unchanged 027 behavior)', () => {
    renderCard([task({ status: 'in_progress' })], { working: true })
    expect(screen.queryByTestId('task-plan-state')).toBeNull()
    expect(screen.queryByTestId('task-plan-resume')).toBeNull()
  })

  it('resume note renders as "Next action"', () => {
    renderCard([task({ status: 'open', resume_note: 'retry the export with paging' })])
    expect(screen.getByTestId('task-plan-note').textContent).toContain(
      'Next action: retry the export with paging',
    )
  })

  it('no-progress streak badges the burn', () => {
    renderCard([
      task({ status: 'open', attempts: 3, last_attempt_outcome: 'no_progress' }),
    ])
    expect(screen.getByTestId('task-plan-ledger').textContent).toBe(
      '3 resume attempts without progress',
    )
  })

  it('aborted resume badges as ceiling-cut resumable', () => {
    renderCard([
      task({ status: 'open', attempts: 1, last_attempt_outcome: 'aborted' }),
    ])
    expect(screen.getByTestId('task-plan-ledger').textContent).toBe(
      'last resume hit its usage ceiling — resumable',
    )
  })

  it('single progress attempt shows no badge', () => {
    renderCard([
      task({ status: 'open', attempts: 1, last_attempt_outcome: 'progress' }),
    ])
    expect(screen.queryByTestId('task-plan-ledger')).toBeNull()
  })

  it('anchored snapshot (no phase-7 fields) still renders', () => {
    render(
      <TaskPlanCard
        tasks={[
          { title: 'step', status: 'done', sort_order: 0 },
          { title: 'step 2', status: 'blocked', blocked_reason: 'ask', sort_order: 1 },
        ]}
        variant="completed"
      />,
    )
    expect(screen.getByTestId('task-plan-card').getAttribute('data-variant')).toBe('completed')
    expect(screen.queryByTestId('task-plan-note')).toBeNull()
    expect(screen.queryByTestId('task-plan-ledger')).toBeNull()
  })
})
