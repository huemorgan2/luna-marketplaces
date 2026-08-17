// @vitest-environment jsdom
// 078 — live tool-run block. ui.live.open puts the code on screen at once,
// append streams output, close stamps the status and the block folds to a
// pill; a bubble holding live runs shows them instead of the persisted iframe.
import { describe, it, expect, vi, beforeEach, beforeAll, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react'
import { renderTimeline } from '../views/ChatPanel'
import { LiveRunBlock, applyLiveEvent, isLiveEvent, type LiveRun } from '../views/LiveRunBlock'

vi.mock('react-markdown', async () => {
  const React = await import('react')
  function MockMarkdown(props: { children?: unknown }) {
    return React.createElement('div', { 'data-testid': 'md' }, String(props.children ?? ''))
  }
  return { default: MockMarkdown, defaultUrlTransform: (u: string) => u }
})
vi.mock('@luna/lib/embedAssets', () => ({
  inlineEmbedAssets: async (html: string) => html,
  forceEagerEmbedImages: (html: string) => html,
}))

type Msg = Parameters<typeof renderTimeline>[0][number]
function msg(over: Partial<Msg> & { id: string }): Msg {
  return { role: 'assistant', content: '', created_at: new Date(1753000000000).toISOString(), ...over } as Msg
}
function draw(messages: Msg[]) {
  return render(<>{renderTimeline(messages, [], 'c1', '🌙', null, 'Luna')}</>)
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
  // reduced motion → code renders complete, no reveal timers to wait on
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (q: string) => ({
      matches: q.includes('reduced-motion'),
      media: q,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  })
})
beforeEach(() => cleanup())
afterEach(() => vi.useRealTimers())

describe('applyLiveEvent reducer', () => {
  it('open → append → close builds one run in order', () => {
    let runs = applyLiveEvent(undefined, { type: 'ui.live.open', id: 'r1', tool: 'code_run', lang: 'python', code: 'print(1)', title: 'Say one' }, 1000)
    expect(runs).toHaveLength(1)
    expect(runs[0]).toMatchObject({ id: 'r1', status: 'running', code: 'print(1)', title: 'Say one', stdout: '' })
    runs = applyLiveEvent(runs, { type: 'ui.live.append', id: 'r1', stream: 'stdout', text: 'a\n' }, 1100)
    runs = applyLiveEvent(runs, { type: 'ui.live.append', id: 'r1', stream: 'stdout', text: 'b\n' }, 1200)
    runs = applyLiveEvent(runs, { type: 'ui.live.append', id: 'r1', stream: 'stderr', text: 'warn' }, 1300)
    expect(runs[0].stdout).toBe('a\nb\n')
    expect(runs[0].stderr).toBe('warn')
    runs = applyLiveEvent(runs, { type: 'ui.live.close', id: 'r1', status: 'success', duration_ms: 420, files: 2, exit_code: 0 }, 2000)
    expect(runs[0]).toMatchObject({ status: 'success', duration_ms: 420, files: 2, exit_code: 0 })
  })
  it('ignores frames for unknown ids and unknown statuses fall back to error', () => {
    const base = applyLiveEvent([], { type: 'ui.live.open', id: 'r1', tool: 'code_run', code: '' }, 0)
    expect(applyLiveEvent(base, { type: 'ui.live.append', id: 'zzz', stream: 'stdout', text: 'x' }, 0)).toBe(base)
    const closed = applyLiveEvent(base, { type: 'ui.live.close', id: 'r1', status: 'weird' }, 5)
    expect(closed[0].status).toBe('error')
  })
  it('isLiveEvent matches only ui.live.*', () => {
    expect(isLiveEvent({ type: 'ui.live.open' })).toBe(true)
    expect(isLiveEvent({ type: 'ui.tasks' })).toBe(false)
    expect(isLiveEvent(null)).toBe(false)
  })
})

function run(over: Partial<LiveRun> = {}): LiveRun {
  return {
    id: 'r1', tool: 'code_run', lang: 'python', code: 'print("hi")\nprint("bye")',
    stdout: '', stderr: '', status: 'running', started_at: Date.now(), ...over,
  }
}

describe('LiveRunBlock', () => {
  it('running: open, spinner, code visible, waiting for output', () => {
    render(<LiveRunBlock run={run({ title: 'Say hi' })} />)
    expect(screen.getByTestId('live-run').getAttribute('data-status')).toBe('running')
    expect(screen.getByTestId('live-run-spinner')).toBeTruthy()
    expect(screen.getByTestId('live-run-title').textContent).toBe('Say hi')
    expect(screen.getByTestId('live-run-code').textContent).toContain('print("bye")')
    expect(screen.getByTestId('live-run-stdout').textContent).toContain('waiting for output')
    expect(screen.getByTestId('live-run-head').textContent).toContain('Running')
  })
  it('streams output and shows the errors pane only when stderr exists', () => {
    const { rerender } = render(<LiveRunBlock run={run({ stdout: 'line 1\n' })} />)
    expect(screen.getByTestId('live-run-stdout').textContent).toBe('line 1\n')
    expect(screen.queryByTestId('live-run-stderr')).toBeNull()
    rerender(<LiveRunBlock run={run({ stdout: 'line 1\nline 2\n', stderr: 'boom' })} />)
    expect(screen.getByTestId('live-run-stdout').textContent).toBe('line 1\nline 2\n')
    expect(screen.getByTestId('live-run-stderr').textContent).toBe('boom')
  })
  it('closes: badge + duration + files, then folds; click re-opens', () => {
    vi.useFakeTimers()
    const { rerender } = render(<LiveRunBlock run={run()} />)
    rerender(<LiveRunBlock run={run({ status: 'success', duration_ms: 6300, files: 0, stdout: 'done' })} />)
    const head = screen.getByTestId('live-run-head')
    expect(screen.getByTestId('live-run-badge').getAttribute('data-status')).toBe('success')
    expect(head.textContent).toContain('6.3 s')
    expect(head.textContent).toContain('0 files')
    // reduced motion → folds immediately (timer 0)
    act(() => { vi.runAllTimers() })
    expect(screen.queryByTestId('live-run-body')).toBeNull()
    fireEvent.click(head)
    expect(screen.getByTestId('live-run-body')).toBeTruthy()
    expect(screen.getByTestId('live-run-stdout').textContent).toBe('done')
  })
  it('error and timeout statuses label honestly', () => {
    const { rerender } = render(<LiveRunBlock run={run({ status: 'error', exit_code: 1, duration_ms: 10 })} />)
    expect(screen.getByTestId('live-run-head').textContent).toContain('failed')
    rerender(<LiveRunBlock run={run({ status: 'timeout', duration_ms: 60000 })} />)
    expect(screen.getByTestId('live-run-head').textContent).toContain('timed out')
  })
})

describe('bubble integration', () => {
  it('a bubble with live runs shows them and hides the persisted iframe card', async () => {
    const { container } = draw([
      msg({ id: 'a1', content: 'Running it.', embed_iframe: '<html>card</html>', live_runs: [run({ status: 'success', duration_ms: 5 })] }),
    ])
    expect(container.querySelector('[data-testid="live-runs"]')).toBeTruthy()
    // no iframe embed while live runs exist
    await new Promise((r) => setTimeout(r, 0))
    expect(container.querySelector('[data-testid="bubble-embed"]')).toBeNull()
  })
  it('a pending bubble with a live run does not show the thinking dots', () => {
    const { container } = draw([msg({ id: 'a1', pending: true, live_runs: [run()] })])
    expect(container.textContent).not.toContain('thinking')
    expect(container.querySelector('[data-testid="live-run"]')).toBeTruthy()
  })
})
