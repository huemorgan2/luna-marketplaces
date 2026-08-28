// @vitest-environment jsdom
// 012 — conversation isolation. A turn that keeps streaming after the user
// switches chats must keep painting ITS OWN conversation: no working-tools
// chrome, no Stop button, and no stream frames may leak into the chat on
// screen; switching back shows everything the turn produced. Global
// message.created events route to their own conversation too.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup, fireEvent } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { CHAT_BRIDGE_EVENT } from '@luna/lib/pluginBridge'

type StreamCbs = {
  onDelta?: (d: string) => void
  onNewMessage?: (id: string) => void
  onToolCall?: (names: string[]) => void
  onDone?: (text: string, meta?: Record<string, unknown>) => void
}
type GlobalHandlers = {
  onMessageCreated?: (info: Record<string, unknown>) => void
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

const C1 = { id: 'c1', title: 'first chat', created_at: '2026-08-28T00:00:00Z', updated_at: '2026-08-28T00:00:00Z' }
const C2 = { id: 'c2', title: 'second chat', created_at: '2026-08-28T01:00:00Z', updated_at: '2026-08-28T01:00:00Z' }

function bridgeSend(text: string) {
  window.dispatchEvent(new CustomEvent(CHAT_BRIDGE_EVENT, { detail: { action: 'send', text } }))
}

async function selectConv(title: string) {
  const item = screen.getAllByTestId('conv-item').find((el) => el.textContent?.includes(title))
  expect(item).toBeTruthy()
  await act(async () => {
    fireEvent.click(item!)
  })
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
  h.createConversation.mockResolvedValue(C2)
})

/** Render, wait for c1 to be selected, start a never-ending stream in c1 and
 *  return its captured callbacks. */
async function startTurnInC1(): Promise<StreamCbs> {
  let cbs: StreamCbs = {}
  h.sendMessageStream.mockImplementation((_conv: string, _text: string, callbacks: StreamCbs) => {
    cbs = callbacks
    return new Promise(() => {}) // the turn never ends
  })
  render(<ChatPanel identity={null} />)
  await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
  await act(async () => {
    bridgeSend('hello from c1')
  })
  await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalled())
  expect(h.sendMessageStream.mock.calls[0][0]).toBe('c1')
  return cbs
}

describe('012 conversation isolation', () => {
  it('a new chat shows a clean composer while another chat is mid-turn', async () => {
    const cbs = await startTurnInC1()
    await act(async () => {
      cbs.onToolCall?.(['browser.open', 'files.read', 'shell.run', 'web.search'])
    })
    // c1 on screen: live chrome is visible.
    expect(screen.getByTestId('working-tool-chips')).toBeTruthy()
    expect(screen.getByText('hello from c1')).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByTestId('new-chat-btn'))
    })
    await waitFor(() => expect(h.createConversation).toHaveBeenCalled())

    // c2 on screen: NO leaked chrome, no leaked bubbles.
    expect(screen.queryByTestId('working-tool-chips')).toBeNull()
    expect(screen.queryByText('working…')).toBeNull()
    expect(screen.queryByText('hello from c1')).toBeNull()
  })

  it('stream frames arriving after the switch stay in their own conversation', async () => {
    const cbs = await startTurnInC1()
    await act(async () => {
      fireEvent.click(screen.getByTestId('new-chat-btn'))
    })
    await waitFor(() => expect(h.createConversation).toHaveBeenCalled())

    // Frames for c1 land while c2 is open.
    await act(async () => {
      cbs.onDelta?.('streamed into c1')
    })
    // rAF flush: give the frame a beat, then assert it did NOT paint here.
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByText(/streamed into c1/)).toBeNull()

    // Switching back to c1 shows the streamed content and the live chrome.
    await selectConv('first chat')
    await waitFor(() => expect(screen.queryByText(/streamed into c1/)).toBeTruthy())
    expect(screen.getByText('hello from c1')).toBeTruthy()
  })

  it('global message.created routes to its own conversation, never the open one', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    // The event's row is persisted server-side — a later refetch of c2
    // returns it (selectConversation reloads from the server).
    h.messages.mockImplementation(async (id: string) =>
      id === 'c2'
        ? [{ id: 'm-bg-1', role: 'assistant', content: 'background reply for c2', created_at: '2026-08-28T02:00:00Z' }]
        : [])
    let handlers: GlobalHandlers = {}
    h.subscribeApprovalEvents.mockImplementation(((hs: GlobalHandlers) => {
      handlers = hs
      return () => {}
    }) as never)
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
    await waitFor(() => expect(h.subscribeApprovalEvents).toHaveBeenCalled())

    // A background message for c2 arrives while c1 is on screen.
    await act(async () => {
      handlers.onMessageCreated?.({
        conversation_id: 'c2',
        message_id: 'm-bg-1',
        content: 'background reply for c2',
        role: 'assistant',
        created_at: '2026-08-28T02:00:00Z',
      })
    })
    expect(screen.queryByText(/background reply for c2/)).toBeNull()

    // An event with NO conversation id is dropped, not defaulted to the open chat.
    await act(async () => {
      handlers.onMessageCreated?.({
        message_id: 'm-bg-2',
        content: 'orphan message',
        role: 'assistant',
        created_at: '2026-08-28T02:00:01Z',
      })
    })
    expect(screen.queryByText(/orphan message/)).toBeNull()

    // Opening c2 shows its message.
    await selectConv('second chat')
    await waitFor(() => expect(screen.queryByText(/background reply for c2/)).toBeTruthy())
    expect(screen.queryByText(/orphan message/)).toBeNull()
  })
})
