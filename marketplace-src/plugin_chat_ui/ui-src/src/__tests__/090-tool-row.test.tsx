// @vitest-environment jsdom
// 016 — the one ToolRow: server tool.called / tool.completed frames paint one
// chip per call that shimmers while running and settles to grey (rose on
// error). The composer only says "working…"; no wrench pill; live auto-
// approval receipts are suppressed so a tool is shown once.
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

const chips = () => screen.queryAllByTestId('tool-chip')
const shimmering = (el: HTMLElement) => !!el.querySelector('.shimmer-text')

describe('016 tool row', () => {
  it('a chip appears on tool.called and shimmers until tool.completed turns it grey', async () => {
    const { cbs } = await startTurn()
    expect(screen.queryByTestId('tool-row')).toBeNull()
    await act(async () => {
      cbs.onUiEvent?.(called('a1', 'files.read'))
    })
    expect(chips().length).toBe(1)
    expect(chips()[0].dataset.status).toBe('running')
    expect(shimmering(chips()[0])).toBe(true)
    await act(async () => {
      cbs.onUiEvent?.(completed('a1', 'files.read'))
    })
    expect(chips()[0].dataset.status).toBe('done')
    expect(shimmering(chips()[0])).toBe(false)
    expect(chips()[0].className).toContain('text-ink-500')
    // No wrench pill, no per-call receipts.
    expect(screen.queryByTestId('tool-progress-chip')).toBeNull()
    expect(screen.queryByTestId('auto-tool-receipts')).toBeNull()
  })

  it('an error turns the chip rose and carries the message; duplicates by call_id are ignored', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('b1', 'shell.run'))
      cbs.onUiEvent?.(called('b1', 'shell.run')) // same id twice → one chip
      cbs.onUiEvent?.(completed('b1', 'shell.run', 'error', 'command not found'))
    })
    expect(chips().length).toBe(1)
    expect(chips()[0].dataset.status).toBe('error')
    expect(chips()[0].className).toContain('rose')
    expect(chips()[0].getAttribute('title')).toContain('command not found')
  })

  it('repeated calls of one tool cluster into one chip with #2 tags; subagent calls join the same row', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('c1a', 'web.search'))
      cbs.onUiEvent?.(completed('c1a', 'web.search'))
      cbs.onUiEvent?.(called('c1b', 'web.search'))
      // a subagent's tool: same frame shape, parent conversation id
      cbs.onUiEvent?.(called('s1', 'files.read'))
    })
    expect(chips().length).toBe(2)
    expect(chips()[0].textContent).toContain('#2')
    expect(chips()[0].dataset.status).toBe('running') // second call still running
    expect(chips()[1].dataset.status).toBe('running')
    expect(screen.getAllByTestId('tool-row').length).toBe(1)
  })

  it('the row survives the end of the turn and resets when the next turn starts', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('d1', 'files.read'))
      cbs.onUiEvent?.(completed('d1', 'files.read'))
      cbs.onDone?.('all done', {})
      endTurn()
    })
    await waitFor(() => expect(screen.queryByTestId('working-line')).toBeNull())
    expect(chips().length).toBe(1)
    expect(chips()[0].dataset.status).toBe('done')
    // next turn → fresh row
    h.sendMessageStream.mockImplementation(() => new Promise<void>(() => {}))
    await act(async () => {
      bridgeSend('again')
    })
    await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalledTimes(2))
    expect(screen.queryByTestId('tool-row')).toBeNull()
  })

  it('a stream that closes mid-call settles the running chip instead of leaving a phantom shimmer', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onUiEvent?.(called('e1', 'files.read'))
      cbs.onDone?.('done', {})
      endTurn()
    })
    await waitFor(() => expect(chips()[0].dataset.status).toBe('done'))
  })

  it('the composer says only "working…" — no tool names', async () => {
    const { cbs } = await startTurn()
    expect(screen.getByTestId('working-line').textContent).toContain('working…')
    await act(async () => {
      cbs.onToolCall?.(['files.read'])
      cbs.onUiEvent?.(called('f1', 'files.read'))
    })
    expect(screen.getByTestId('working-line').textContent).not.toContain('files.read')
    expect(screen.queryByTestId('working-tool-chips')).toBeNull()
  })

  it('an auto-approval decided during the live turn adds no receipt row (the chip already shows it)', async () => {
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
    expect(chips().length).toBe(1)
    expect(screen.queryByTestId('auto-tool-receipts')).toBeNull()
  })
})
