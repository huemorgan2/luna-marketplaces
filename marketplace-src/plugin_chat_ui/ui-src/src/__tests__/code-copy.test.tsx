// @vitest-environment jsdom
// Fenced code blocks in assistant replies render a hover copy button that
// puts the block's text on the clipboard and flips to a "Copied!" check.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'

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

beforeEach(() => {
  cleanup()
  localStorage.clear()
  Element.prototype.scrollTo = () => {}
  h.identity.mockResolvedValue({ name: 'Luna', emoji: '🌙' })
  h.conversations.mockResolvedValue([
    { id: 'c1', title: 'chat', created_at: '2026-08-06T00:00:00Z', updated_at: '2026-08-06T00:00:00Z' },
  ])
})

const CODE = 'echo "hello"\nls -la'

function assistantMessageWithCode() {
  return [
    {
      id: 'm1',
      role: 'assistant',
      content: 'Run this:\n\n```bash\n' + CODE + '\n```\n\nthen check the output.',
      created_at: '2026-08-06T00:00:01Z',
    },
  ]
}

describe('code block copy button', () => {
  it('copies the block text to the clipboard and shows the copied state', async () => {
    const writeText = vi.fn(async (_text: string) => {})
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    h.messages.mockResolvedValue(assistantMessageWithCode())

    render(<ChatPanel identity={null} initialConversationId="c1" />)
    const btn = await screen.findByLabelText('Copy code')
    fireEvent.click(btn)

    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect((writeText.mock.calls[0]?.[0] ?? '').trim()).toBe(CODE)
    await waitFor(() => expect(btn.getAttribute('title')).toBe('Copied!'))
  })

  it('falls back to execCommand when the clipboard API rejects', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => { throw new Error('no focus') }) },
      configurable: true,
    })
    const exec = vi.fn(() => true)
    document.execCommand = exec as unknown as typeof document.execCommand
    h.messages.mockResolvedValue(assistantMessageWithCode())

    render(<ChatPanel identity={null} initialConversationId="c1" />)
    const btn = await screen.findByLabelText('Copy code')
    fireEvent.click(btn)

    await waitFor(() => expect(exec).toHaveBeenCalledWith('copy'))
    await waitFor(() => expect(btn.getAttribute('title')).toBe('Copied!'))
  })
})
