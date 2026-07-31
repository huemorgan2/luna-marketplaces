// @vitest-environment jsdom
// 065 — chat bridge landing in the rich panel: the shell re-dispatches a
// validated luna-chat message as the CHAT_BRIDGE_EVENT CustomEvent; ChatPanel
// prefills + focuses the composer, and 'send' fires a real turn with the
// override text while the composer draft survives.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { CHAT_BRIDGE_EVENT } from '@luna/lib/pluginBridge'

const h = vi.hoisted(() => ({
  conversations: vi.fn(),
  messages: vi.fn(),
  identity: vi.fn(),
  createConversation: vi.fn(),
  sendMessageStream: vi.fn(),
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
    createConversation: h.createConversation,
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
    setModelChain: vi.fn(),
    resumePlan: vi.fn(),
    dismissPlan: vi.fn(),
  },
  getToken: () => null,
  setToken: () => {},
  sendMessageStream: h.sendMessageStream,
  continueConversation: vi.fn(),
  startOnboardingStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
  queueMessage: vi.fn(),
  stopTurn: vi.fn(),
  getTurnStatus: vi.fn(async () => ({ active: false })),
  attachTurnStream: vi.fn(),
}))

function bridge(action: string, text = '') {
  window.dispatchEvent(new CustomEvent(CHAT_BRIDGE_EVENT, { detail: { action, text } }))
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  h.identity.mockResolvedValue({ name: 'Luna', emoji: '🌙' })
  h.conversations.mockResolvedValue([])
  h.messages.mockResolvedValue([])
  h.createConversation.mockResolvedValue({
    id: 'c1', title: null,
    created_at: '2026-07-31T00:00:00Z', updated_at: '2026-07-31T00:00:00Z',
  })
  h.sendMessageStream.mockImplementation(
    async (_id: string, _text: string, hs: { onDone: (t: string, m?: unknown) => void }) => {
      hs.onDone('', undefined)
    },
  )
})

async function composer() {
  return (await screen.findByPlaceholderText(/Message /)) as HTMLTextAreaElement
}

describe('065 chat bridge → ChatPanel', () => {
  it('prefill fills and focuses the composer', async () => {
    render(<ChatPanel identity={null} />)
    const box = await composer()
    bridge('prefill', 'Change this step: X — ')
    await waitFor(() => expect(box.value).toBe('Change this step: X — '))
    expect(document.activeElement).toBe(box)
  })

  it('focus only focuses, leaving the draft alone', async () => {
    render(<ChatPanel identity={null} />)
    const box = await composer()
    fireEvent.change(box, { target: { value: 'my draft' } })
    box.blur()
    bridge('focus')
    await waitFor(() => expect(document.activeElement).toBe(box))
    expect(box.value).toBe('my draft')
  })

  it('send fires a turn with the override text and keeps the draft', async () => {
    render(<ChatPanel identity={null} />)
    const box = await composer()
    fireEvent.change(box, { target: { value: 'half-typed draft' } })
    bridge('send', 'do the thing')
    await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalledTimes(1))
    expect(h.sendMessageStream.mock.calls[0][0]).toBe('c1')
    expect(h.sendMessageStream.mock.calls[0][1]).toBe('do the thing')
    expect(box.value).toBe('half-typed draft')
  })
})
