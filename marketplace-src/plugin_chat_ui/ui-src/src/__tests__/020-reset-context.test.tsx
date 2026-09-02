// @vitest-environment jsdom
// 020 — per-conversation context reset. Two entry points: a "Reset context"
// section in the Chat settings dialog (two-step confirm, works on every chat
// including ops) and a `/reset` composer command. Both call
// POST /api/conversations/{id}/reset-context; nothing is deleted — the
// transcript stays, the model just stops seeing the old messages.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup, fireEvent } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'

const h = vi.hoisted(() => ({
  planTasks: vi.fn(),
  conversations: vi.fn(),
  messages: vi.fn(),
  context: vi.fn(async () => null),
  identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
  createConversation: vi.fn(),
  sendMessageStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
  resetContext: vi.fn(),
  subscribeConvStateEvents: vi.fn(() => () => {}),
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
    renameConversation: vi.fn(async () => {}),
    deleteConversation: vi.fn(async () => {}),
    resetContext: h.resetContext,
    condense: vi.fn(),
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

vi.mock('../lib/convState', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../lib/convState')>()
  return {
    ...orig,
    patchConversationState: vi.fn(async () => {}),
    subscribeConvStateEvents: h.subscribeConvStateEvents,
  }
})

const OPS = {
  id: 'o-1', title: 'ops chat', kind: 'ops', state: 'building',
  created_at: '2026-08-28T01:00:00Z', updated_at: '2026-08-28T01:00:00Z',
}

const CTX = { used_tokens: 12, max_tokens: 200000, percent: 0.006 }

async function openSettings() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
  })
  await act(async () => {
    fireEvent.click(screen.getByTestId('chat-header-settings'))
  })
  return screen.getByTestId('chat-settings-dialog')
}

async function renderPanel() {
  render(<ChatPanel identity={null} />)
  await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
}

async function typeAndSend(text: string) {
  const ta = screen.getByPlaceholderText(/Message Luna/) as HTMLTextAreaElement
  fireEvent.change(ta, { target: { value: text } })
  await act(async () => {
    fireEvent.keyDown(ta, { key: 'Enter' })
  })
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  h.conversations.mockResolvedValue([OPS])
  h.messages.mockResolvedValue([
    { id: 'm-1', role: 'assistant', content: 'old reply', created_at: '2026-08-28T01:01:00Z' },
  ])
  h.context.mockResolvedValue(null)
  h.planTasks.mockResolvedValue({ tasks: [], created_at: null, turn_active: false })
  h.resetContext.mockResolvedValue({ reset: true, context: CTX })
  h.subscribeConvStateEvents.mockImplementation((() => () => {}) as never)
})

describe('020 reset from Chat settings', () => {
  it('two-step confirm calls the API, shows done, and drops a system line', async () => {
    await renderPanel()
    await openSettings()
    // Step 1 arms the confirm; nothing is called yet.
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset'))
    })
    expect(h.resetContext).not.toHaveBeenCalled()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset-confirm'))
    })
    expect(h.resetContext).toHaveBeenCalledWith('o-1')
    expect(screen.getByTestId('chat-settings-reset-done')).toBeTruthy()
    // The transcript notes the reset; the old message is still visible.
    expect(screen.getByText(/Context reset — earlier messages stay in the transcript/)).toBeTruthy()
    expect(screen.getByText('old reply')).toBeTruthy()
  })

  it('the section exists on ops chats (the one chat that cannot be deleted)', async () => {
    await renderPanel()
    await openSettings()
    expect(screen.getByTestId('chat-settings-reset')).toBeTruthy()
    expect(screen.queryByTestId('chat-settings-delete')).toBeNull()
  })

  it('Cancel disarms the confirm without calling the API', async () => {
    await renderPanel()
    await openSettings()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset'))
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset-cancel'))
    })
    expect(h.resetContext).not.toHaveBeenCalled()
    expect(screen.getByTestId('chat-settings-reset')).toBeTruthy()
  })

  it('a 404 shows the unsupported-server message instead of resetting', async () => {
    h.resetContext.mockRejectedValue(new Error('HTTP 404'))
    await renderPanel()
    await openSettings()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset'))
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-reset-confirm'))
    })
    expect(screen.getByTestId('chat-settings-reset-error').textContent)
      .toContain("doesn't support context reset")
    expect(screen.queryByTestId('chat-settings-reset-done')).toBeNull()
  })
})

describe('020 /reset composer command', () => {
  it('resets without sending anything to the agent', async () => {
    await renderPanel()
    await typeAndSend('/reset')
    await waitFor(() => expect(h.resetContext).toHaveBeenCalledWith('o-1'))
    expect(h.sendMessageStream).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(screen.getByText(/Context reset — earlier messages stay in the transcript/)).toBeTruthy())
  })

  it('reports when there is nothing to reset', async () => {
    h.resetContext.mockResolvedValue({ reset: false, context: CTX })
    await renderPanel()
    await typeAndSend('/reset')
    await waitFor(() =>
      expect(screen.getByText(/Nothing to reset — the conversation has no messages\./)).toBeTruthy())
  })
})
