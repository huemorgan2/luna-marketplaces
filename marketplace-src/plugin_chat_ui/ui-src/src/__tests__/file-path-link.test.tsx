// @vitest-environment jsdom
// A workspace file path in an assistant reply (inline code like
// `/findings/report.md`, or a markdown link with a bare-path href) is
// clickable and posts a luna-navigate message that opens the Files pane
// revealed at that path. Slash-command-looking code (`/loop`), /api asset
// routes, and fenced blocks stay plain.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
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

function assistantMessage(content: string) {
  return [{ id: 'm1', role: 'assistant', content, created_at: '2026-08-06T00:00:01Z' }]
}

function captureNavigate() {
  const posted: any[] = []
  const spy = vi.spyOn(window, 'postMessage').mockImplementation(((msg: any) => {
    if (msg && msg.type === 'luna-navigate') posted.push(msg)
  }) as any)
  return { posted, spy }
}

describe('workspace file path links', () => {
  it('inline-code path posts luna-navigate to the files section on click', async () => {
    const { posted, spy } = captureNavigate()
    h.messages.mockResolvedValue(
      assistantMessage("I've written a finding to `/findings/playbook-status-check-2026-09-02.md` with the evidence."),
    )
    render(<ChatPanel identity={null} initialConversationId="c1" />)

    const link = await screen.findByTestId('file-path-link')
    expect(link.textContent).toBe('/findings/playbook-status-check-2026-09-02.md')
    fireEvent.click(link)

    expect(posted).toEqual([
      { type: 'luna-navigate', section: 'files', target: '/findings/playbook-status-check-2026-09-02.md' },
    ])
    spy.mockRestore()
  })

  it('markdown link with a bare workspace href opens Files instead of navigating', async () => {
    const { posted, spy } = captureNavigate()
    h.messages.mockResolvedValue(assistantMessage('See [the report](/findings/report.md) for details.'))
    render(<ChatPanel identity={null} initialConversationId="c1" />)

    const link = await screen.findByTestId('file-path-link')
    fireEvent.click(link)

    expect(posted).toEqual([
      { type: 'luna-navigate', section: 'files', target: '/findings/report.md' },
    ])
    spy.mockRestore()
  })

  it('trailing copy icon puts the path on the clipboard', async () => {
    const { spy } = captureNavigate()
    const writeText = vi.fn(async (_text: string) => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    h.messages.mockResolvedValue(assistantMessage('Saved to `/findings/report.md` just now.'))
    render(<ChatPanel identity={null} initialConversationId="c1" />)

    const btn = await screen.findByTestId('file-path-copy')
    fireEvent.click(btn)

    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('/findings/report.md'))
    await vi.waitFor(() => expect(btn.getAttribute('title')).toBe('Copied!'))
    spy.mockRestore()
  })

  it('leaves slash commands, /api routes, and fenced blocks alone', async () => {
    const { spy } = captureNavigate()
    h.messages.mockResolvedValue(
      assistantMessage(
        'Try `/loop` or [img](/api/p/plugin-files/read/x.png):\n\n```\n/findings/in-a-block.md\n```\n',
      ),
    )
    render(<ChatPanel identity={null} initialConversationId="c1" />)

    await screen.findByText('/loop')
    expect(screen.queryByTestId('file-path-link')).toBeNull()
    spy.mockRestore()
  })
})
