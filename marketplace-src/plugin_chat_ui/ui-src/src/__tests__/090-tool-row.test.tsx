// @vitest-environment jsdom
// 016/017/022 — the in-turn tool surface. 022: a collapsed summary line
// (loader while the turn runs · wrench that pulses while a tool is active ·
// call counter · whole-turn timer · chevron) that expands into the per-call
// list — one cluster per line, 5-line cap, no per-row spinners/timers. The
// timer freezes at the turn's final elapsed and the line persists as the
// receipt until the next turn starts.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { CHAT_BRIDGE_EVENT } from '@luna/lib/pluginBridge'

type StreamCbs = {
  onDelta?: (d: string) => void
  onToolCall?: (names: string[]) => void
  onToolResult?: (name: string, result: string, embed?: Record<string, string>) => void
  onDone?: (text: string, meta?: Record<string, unknown>) => void
  onUiEvent?: (evt: { type: string; [key: string]: any }) => void
}

const h = vi.hoisted(() => ({
  planTasks: vi.fn(),
  conversations: vi.fn(),
  messages: vi.fn(),
  context: vi.fn(async () => null),
  identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
  createConversation: vi.fn(),
  sendMessageStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
}))

vi.mock('@luna/lib/api', () => ({
  api: {
    planTasks: h.planTasks,
    conversations: h.conversations,
    messages: h.messages,
    context: h.context,
    identity: h.identity,
    authStatus: vi.fn(async () => ({ has_account: true, onboarding_complete: true })),
    models: vi.fn(async () => []),
    modelCatalog: vi.fn(async () => ({ catalog: { reasoning: [] }, configured_providers: [] })),
    approvals: { list: vi.fn(async () => []) },
    secretRequests: { list: vi.fn(async () => []) },
    createConversation: h.createConversation,
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setModelChain: vi.fn(),
    resumePlan: vi.fn(),
    dismissPlan: vi.fn(),
  },
  getToken: () => null,
  setToken: () => {},
  cardAction: vi.fn(),
  uploadAttachment: vi.fn(),
  sendMessageStream: h.sendMessageStream,
  continueConversation: vi.fn(async () => {}),
  startOnboardingStream: vi.fn(),
  subscribeApprovalEvents: h.subscribeApprovalEvents,
  queueMessage: vi.fn(),
  stopTurn: vi.fn(),
  getTurnStatus: vi.fn(async () => ({ active: false })),
  attachTurnStream: vi.fn(),
}))

// Keep the 089 SSE module quiet in this suite (no real network).
vi.mock('../lib/convState', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../lib/convState')>()
  return {
    ...orig,
    patchConversationState: vi.fn(async () => {}),
    subscribeConvStateEvents: vi.fn(() => () => {}),
  }
})

const C1 = { id: 'c1', title: 'first chat', created_at: '2026-08-28T00:00:00Z', updated_at: '2026-08-28T00:00:00Z' }

function bridgeSend(text: string) {
  window.dispatchEvent(new CustomEvent(CHAT_BRIDGE_EVENT, { detail: { action: 'send', text } }))
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  h.conversations.mockResolvedValue([C1])
  h.messages.mockResolvedValue([])
  h.context.mockResolvedValue(null)
  h.planTasks.mockResolvedValue({ tasks: [], created_at: null, turn_active: false })
})

type ApprovalCbs = {
  onRequested?: (r: any) => void
  onDecided?: (info: any) => void
}
let approvalCbs: ApprovalCbs = {}

/** Render, start a turn in c1, return its stream callbacks + an end-turn knob. */
async function startTurn(): Promise<{ cbs: StreamCbs; endTurn: () => void }> {
  let cbs: StreamCbs = {}
  let endTurn: () => void = () => {}
  h.subscribeApprovalEvents.mockImplementation(((handlers: ApprovalCbs) => {
    approvalCbs = handlers
    return () => {}
  }) as any)
  h.sendMessageStream.mockImplementation((_conv: string, _text: string, callbacks: StreamCbs) => {
    cbs = callbacks
    return new Promise<void>((resolve) => { endTurn = () => resolve() })
  })
  render(<ChatPanel identity={null} />)
  await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
  await act(async () => {
    bridgeSend('do things')
  })
  await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalled())
  return { cbs, endTurn }
}

const called = (id: string, name: string) => ({ type: 'tool.called', call_id: id, name, status: 'pending', conversation_id: 'c1' })
const completed = (id: string, name: string, status = 'ok', error?: string) =>
  ({ type: 'tool.completed', call_id: id, name, status, error, conversation_id: 'c1' })

const summary = () => screen.getByTestId('tool-summary')
const openList = async () => { await act(async () => { summary().click() }) }
const rows = () => screen.queryAllByTestId('tool-chip')
const count = () => screen.getByTestId('tool-count').textContent
const shimmering = (el: HTMLElement) => !!el.querySelector('.shimmer-text')
const wrench = () => summary().querySelector('svg.lucide-wrench') as SVGElement

describe('022 tool summary', () => {
  it('closed: loader while live, wrench pulses while a tool runs, counter ticks per call', async () => {
    const { cbs } = await startTurn()
    expect(screen.getByTestId('turn-loader')).toBeTruthy()
    expect(count()).toBe('0')
    expect(wrench().classList.contains('shimmer-icon')).toBe(false)
    await act(async () => { cbs.onUiEvent?.(called('a1', 'files.read')) })
    expect(count()).toBe('1')
    expect(wrench().classList.contains('shimmer-icon')).toBe(true)
    await act(async () => { cbs.onUiEvent?.(completed('a1', 'files.read')) })
    expect(count()).toBe('1')
    expect(wrench().classList.contains('shimmer-icon')).toBe(false)
    await act(async () => { cbs.onUiEvent?.(called('a2', 'shell.run')) })
    expect(count()).toBe('2')
    // no list, no chips while closed
    expect(screen.queryByTestId('tool-list')).toBeNull()
    expect(rows()).toHaveLength(0)
    // no legacy surfaces
    expect(screen.queryByTestId('tool-progress-chip')).toBeNull()
    expect(screen.queryByTestId('auto-tool-receipts')).toBeNull()
  })

  it('the chevron opens the capped list (one row per call) and closes it again', async () => {
    const { cbs } = await startTurn()
    // no calls yet → the summary is not expandable
    await act(async () => { summary().click() })
    expect(screen.queryByTestId('tool-list')).toBeNull()
    await act(async () => {
      cbs.onUiEvent?.(called('b1', 'files.read'))
      cbs.onUiEvent?.(completed('b1', 'files.read'))
      cbs.onUiEvent?.(called('b2', 'shell.run'))
    })
    await openList()
    const list = screen.getByTestId('tool-list')
    expect(list.className).toContain('max-h-[100px]')
    expect(list.className).toContain('overflow-y-auto')
    expect(rows()).toHaveLength(2)
    expect(rows()[0].dataset.status).toBe('done')
    expect(rows()[1].dataset.status).toBe('running')
    expect(shimmering(rows()[1])).toBe(true)
    // rows carry no spinner and no timer — live state is header-only
    expect(list.querySelector('.animate-spin')).toBeNull()
    expect(list.querySelector('[data-testid="turn-live"]')).toBeNull()
    await openList()
    expect(screen.queryByTestId('tool-list')).toBeNull()
  })

  it('repeated calls of one tool cluster into one row with #2 tags; subagent calls join the same log', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('c1a', 'web.search'))
      cbs.onUiEvent?.(completed('c1a', 'web.search'))
      cbs.onUiEvent?.(called('c1b', 'web.search'))
      cbs.onUiEvent?.(called('s1', 'files.read'))
    })
    expect(count()).toBe('3')
    await openList()
    expect(rows()).toHaveLength(2)
    expect(rows()[0].textContent).toContain('#2')
    expect(rows()[0].dataset.status).toBe('running')
    expect(rows()[1].dataset.status).toBe('running')
    expect(screen.getAllByTestId('tool-row').length).toBe(1)
  })

  it('the summary survives the turn with a frozen timer and resets when the next turn starts', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('d1', 'files.read'))
      cbs.onUiEvent?.(completed('d1', 'files.read'))
      cbs.onDone?.('all done', {})
      endTurn()
    })
    await waitFor(() => expect(screen.queryByTestId('turn-loader')).toBeNull())
    // frozen receipt: count + timer stay, loader gone
    expect(count()).toBe('1')
    expect(screen.getByTestId('turn-live').textContent).toMatch(/^\d+:\d\d$/)
    await openList()
    expect(rows()).toHaveLength(1)
    expect(rows()[0].dataset.status).toBe('done')
    // next turn → fresh summary
    h.sendMessageStream.mockImplementation(() => new Promise<void>(() => {}))
    await act(async () => { bridgeSend('again') })
    await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalledTimes(2))
    expect(count()).toBe('0')
    expect(screen.queryByTestId('tool-list')).toBeNull()
    expect(screen.getByTestId('turn-loader')).toBeTruthy()
  })

  it('a stream that closes mid-call settles the running row instead of leaving a phantom shimmer', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('e1', 'files.read'))
      cbs.onDone?.('done', {})
      endTurn()
    })
    await waitFor(() => expect(screen.queryByTestId('turn-loader')).toBeNull())
    expect(wrench().classList.contains('shimmer-icon')).toBe(false)
    await openList()
    await waitFor(() => expect(rows()[0].dataset.status).toBe('done'))
  })

  it('017: the composer shows no words while streaming; the ticking timer lives in the header', async () => {
    const { endTurn } = await startTurn()
    expect(screen.queryByTestId('working-line')).toBeNull()
    expect(screen.queryByText('working…')).toBeNull()
    const live = screen.getByTestId('turn-live')
    expect(live.getAttribute('data-stalled')).toBe('false')
    expect(live.textContent).toMatch(/^\d+:\d\d$/)
    expect(summary().contains(live)).toBe(true)
    await act(async () => { endTurn() })
    // a turn with zero tool calls leaves no receipt at all
    await waitFor(() => expect(screen.queryByTestId('tool-row')).toBeNull())
  })

  it('017: hints render in the open list; tool.called upserts pending → awaiting → running, awaiting surfaces on the closed header', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.({ ...called('m1', 'monday_list_boards'), hint: 'Sales Q3' })
    })
    await openList()
    expect(rows()).toHaveLength(1)
    expect(rows()[0].getAttribute('data-status')).toBe('running')
    expect(screen.getByTestId('tool-hint').textContent).toContain('Sales Q3')
    await act(async () => {
      cbs.onUiEvent?.({ ...called('m1', 'monday_list_boards'), status: 'awaiting_approval' })
    })
    expect(rows()[0].getAttribute('data-status')).toBe('awaiting')
    expect(rows()[0].textContent).toContain('waiting for approval')
    expect(shimmering(rows()[0])).toBe(false)
    // the closed header carries the awaiting state too
    expect(summary().textContent).toContain('waiting for approval')
    await act(async () => {
      cbs.onUiEvent?.({ ...called('m1', 'monday_list_boards'), status: 'running' })
    })
    expect(rows()[0].getAttribute('data-status')).toBe('running')
    expect(shimmering(rows()[0])).toBe(true)
    await act(async () => { cbs.onUiEvent?.(completed('m1', 'monday_list_boards')) })
    expect(rows()[0].getAttribute('data-status')).toBe('done')
    expect(screen.getByTestId('tool-hint').textContent).toContain('Sales Q3')
  })

  it('017: rejected and skipped calls get a grey tagged row instead of vanishing', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('r1', 'delete_file'))
      cbs.onUiEvent?.(completed('r1', 'delete_file', 'rejected', 'policy=block'))
      cbs.onUiEvent?.(called('s1', 'set_value'))
      cbs.onUiEvent?.(completed('s1', 'set_value', 'skipped', 'pre_gate_check'))
    })
    await openList()
    const [r, sk] = rows()
    expect(r.getAttribute('data-status')).toBe('rejected')
    expect(r.textContent).toContain('rejected')
    expect(sk.getAttribute('data-status')).toBe('skipped')
    expect(sk.textContent).toContain('skipped')
  })

  it('017/D2: clicking an error row shows its error under the list', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('e1', 'monday_list_boards'))
      cbs.onUiEvent?.(completed('e1', 'monday_list_boards', 'error', 'no credential for monday'))
    })
    await openList()
    expect(rows()[0].dataset.status).toBe('error')
    expect(rows()[0].getAttribute('title')).toContain('no credential for monday')
    expect(screen.queryByTestId('tool-error')).toBeNull()
    await act(async () => { rows()[0].click() })
    expect(screen.getByTestId('tool-error').textContent).toContain('no credential for monday')
    await act(async () => { rows()[0].click() })
    expect(screen.queryByTestId('tool-error')).toBeNull()
  })

  it('017/D3: after 20 s without a frame the header timer says "still working"; a delta resets it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      const { cbs } = await startTurn()
      await act(async () => { vi.advanceTimersByTime(21_000) })
      const live = screen.getByTestId('turn-live')
      expect(live.getAttribute('data-stalled')).toBe('true')
      expect(live.textContent).toContain('still working')
      await act(async () => { cbs.onDelta?.('hello') })
      await act(async () => { vi.advanceTimersByTime(1_000) })
      expect(screen.getByTestId('turn-live').getAttribute('data-stalled')).toBe('false')
      expect(screen.getByTestId('turn-live').textContent).not.toContain('still working')
    } finally {
      vi.useRealTimers()
    }
  })

  it('an auto-approval decided during the live turn adds no receipt row (the summary already shows it)', async () => {
    const { cbs } = await startTurn()
    const req = {
      id: 'r1', conversation_id: 'c1', summary: 'Read file', status: 'pending',
      requested_at: new Date().toISOString(), payload: { tool: 'files.read' },
    }
    await act(async () => {
      cbs.onUiEvent?.(called('g1', 'files.read'))
      approvalCbs.onRequested?.(req)
      approvalCbs.onDecided?.({ request_id: 'r1', decision: 'approved', auto: true, decided_by: 'auto', conversation_id: 'c1' })
      cbs.onUiEvent?.(completed('g1', 'files.read'))
    })
    expect(count()).toBe('1')
    expect(screen.queryByTestId('auto-tool-receipts')).toBeNull()
  })
})
