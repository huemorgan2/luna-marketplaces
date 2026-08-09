// @vitest-environment jsdom
// 045/phase05 — plan card first paint (= 044 Bug 20).
// The backfill effect is mount-only; the activeId null→id flip on first load
// must NOT cancel the first /planTasks response, the card must render from the
// localStorage seed before the network resolves, and it must not be gated
// behind loadingMessages.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { cachedSet } from '@luna/lib/cache'

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const h = vi.hoisted(() => ({
  planTasks: vi.fn(),
  conversations: vi.fn(),
  messages: vi.fn(),
  context: vi.fn(async () => null),
  identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
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
    createConversation: vi.fn(),
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setModelChain: vi.fn(),
    resumePlan: vi.fn(),
    dismissPlan: vi.fn(),
  },
  getToken: () => null,
  setToken: () => {},
  sendMessageStream: vi.fn(),
  continueConversation: vi.fn(),
  startOnboardingStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
  queueMessage: vi.fn(),
  stopTurn: vi.fn(),
  getTurnStatus: vi.fn(async () => ({ active: false })),
  attachTurnStream: vi.fn(),
}))

const CONV = { id: 'c1', title: 'chat', created_at: '2026-07-21T00:00:00Z', updated_at: '2026-07-21T00:00:00Z' }
const TASK = { id: 't1', intent: 'Ship it', title: 'step one', status: 'open' as const, blocked_reason: null, sort_order: 0 }

beforeAll(() => {
  // jsdom has no Element.scrollTo.
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  h.conversations.mockResolvedValue([CONV])
  h.messages.mockResolvedValue([])
  h.context.mockResolvedValue(null)
})

describe('045 plan card first paint', () => {
  it('renders from the FIRST fetch; the activeId null→id flip does not drop the response', async () => {
    const d = deferred<{ tasks: (typeof TASK)[]; created_at: string; turn_active: boolean }>()
    h.planTasks.mockReturnValue(d.promise)
    render(<ChatPanel identity={null} />)
    // Wait until the conversation got selected (activeId flipped null→c1).
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
    // Only now does the (single) plan fetch resolve — the old [activeId]-keyed
    // effect would have cancelled it during the flip.
    await act(async () => {
      d.resolve({ tasks: [TASK], created_at: '2026-07-21T01:00:00Z', turn_active: false })
    })
    expect(await screen.findByTestId('task-plan-card')).toBeTruthy()
    expect(h.planTasks).toHaveBeenCalledTimes(1)
  })

  it('renders from the localStorage seed before the network resolves', async () => {
    cachedSet('plan-tasks', { tasks: [TASK], created_at: '2026-07-21T01:00:00Z' })
    h.planTasks.mockReturnValue(new Promise(() => {})) // network never answers
    render(<ChatPanel identity={null} />)
    expect(await screen.findByTestId('task-plan-card')).toBeTruthy()
  })

  it('is not gated by loadingMessages', async () => {
    h.messages.mockReturnValue(new Promise(() => {})) // messages hang forever
    h.planTasks.mockResolvedValue({ tasks: [TASK], created_at: '2026-07-21T01:00:00Z', turn_active: false })
    render(<ChatPanel identity={null} />)
    expect(await screen.findByTestId('task-plan-card')).toBeTruthy()
  })

  it('renders in the plan\'s home conversation, not in others', async () => {
    h.planTasks.mockResolvedValue({
      tasks: [TASK],
      created_at: '2026-07-21T01:00:00Z',
      conversation_id: 'c1',
      turn_active: false,
    })
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
    expect(await screen.findByTestId('task-plan-card')).toBeTruthy()
  })

  it('stays hidden in a conversation that is not the plan\'s home', async () => {
    h.planTasks.mockResolvedValue({
      tasks: [TASK],
      created_at: '2026-07-21T01:00:00Z',
      conversation_id: 'c-other',
      turn_active: false,
    })
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(h.planTasks).toHaveBeenCalled())
    expect(screen.queryByTestId('task-plan-card')).toBeNull()
  })

  it('scrolls with the chat — the live card row is not sticky', async () => {
    h.planTasks.mockResolvedValue({
      tasks: [TASK],
      created_at: '2026-07-21T01:00:00Z',
      conversation_id: 'c1',
      turn_active: false,
    })
    render(<ChatPanel identity={null} />)
    const card = await screen.findByTestId('task-plan-card')
    expect(card.parentElement?.className ?? '').not.toContain('sticky')
  })
})
