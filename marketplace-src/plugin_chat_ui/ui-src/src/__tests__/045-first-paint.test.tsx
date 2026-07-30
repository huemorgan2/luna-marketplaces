// @vitest-environment jsdom
// 045/phase05 — first-paint parallelism. A /chat/:id deep link must fetch its
// messages WITHOUT waiting for the conversations list, and refreshIdentity
// must dedupe concurrent callers (Shell mount + identityStore init used to
// race two identical /api/identity requests).
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, fireEvent, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { refreshIdentity } from '@luna/lib/identityStore'

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

const h = vi.hoisted(() => ({
  conversations: vi.fn(),
  messages: vi.fn(),
  identity: vi.fn(),
}))

vi.mock('@luna/lib/api', () => ({
  api: {
    planTasks: vi.fn(async () => ({ tasks: [], created_at: null, turn_active: false })),
    conversations: h.conversations,
    messages: h.messages,
    context: vi.fn(async () => null),
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

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  h.identity.mockResolvedValue({ name: 'Luna', emoji: '🌙' })
})

describe('045 first paint', () => {
  // Runs FIRST, before any ChatPanel mount touches the identity store.
  it('refreshIdentity dedupes concurrent callers, then allows the next refresh', async () => {
    const d = deferred<{ name: string; emoji: string }>()
    h.identity.mockReturnValue(d.promise)
    const before = h.identity.mock.calls.length
    refreshIdentity()
    refreshIdentity()
    refreshIdentity()
    expect(h.identity.mock.calls.length - before).toBe(1)
    d.resolve({ name: 'Luna', emoji: '🌙' })
    await d.promise
    await new Promise((r) => setTimeout(r, 0)) // let .then/.finally clear the in-flight flag
    refreshIdentity()
    expect(h.identity.mock.calls.length - before).toBe(2)
  })

  it('deep link fetches messages without awaiting the conversations list', async () => {
    // The conversations list NEVER resolves — the URL conversation's messages
    // fetch must fire anyway (they run in parallel now).
    h.conversations.mockReturnValue(new Promise(() => {}))
    h.messages.mockResolvedValue([])
    render(<ChatPanel identity={null} initialConversationId="c9" />)
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c9'))
  })

  it('a message sent while the initial load is in flight survives the snapshot overwrite', async () => {
    // Dojo catch (phase05): the composer is interactive before api.messages
    // resolves; the resolving (empty) snapshot must not wipe the just-sent
    // user bubble and its streaming assistant bubble.
    const d = deferred<never[]>()
    h.conversations.mockResolvedValue([
      { id: 'c9', title: 'chat', created_at: '2026-07-21T00:00:00Z', updated_at: '2026-07-21T00:00:00Z' },
    ])
    h.messages.mockReturnValue(d.promise)
    render(<ChatPanel identity={null} initialConversationId="c9" />)
    const box = (await screen.findByPlaceholderText(/Message /)) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'hello from the race' } })
    fireEvent.keyDown(box, { key: 'Enter', code: 'Enter' })
    await screen.findByText('hello from the race')
    await act(async () => {
      d.resolve([])
    })
    expect(screen.getByText('hello from the race')).toBeTruthy()
  })
})
