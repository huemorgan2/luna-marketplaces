// @vitest-environment jsdom
// 019 — per-chat tints + the Chat settings dialog + the chat-name header.
// The active chat's tint washes the message pane and colors the agent's plain
// bubbles; ops chats default to amber, everything else to no tint. Picks are
// made in the header-menu Chat settings dialog (which also renames and
// deletes) and persist per-browser in localStorage. The header titles the
// CHAT (not the agent); with no conversation list beside the chat (compact)
// the title becomes a pulldown that switches chats.
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
  renameConversation: vi.fn(async () => {}),
  deleteConversation: vi.fn(async () => {}),
  patchConversationState: vi.fn(async () => {}),
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
    renameConversation: h.renameConversation,
    deleteConversation: h.deleteConversation,
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
    patchConversationState: h.patchConversationState,
    subscribeConvStateEvents: h.subscribeConvStateEvents,
  }
})

const BUILD = {
  id: 'b-1', title: 'build chat', kind: 'building', state: 'building',
  created_at: '2026-08-28T02:00:00Z', updated_at: '2026-08-28T02:00:00Z',
}
const OPS = {
  id: 'o-1', title: 'ops chat', kind: 'ops', state: 'building',
  created_at: '2026-08-28T01:00:00Z', updated_at: '2026-08-28T01:00:00Z',
}

function pane(): HTMLElement {
  return screen.getByTestId('chat-area')
}

/** The bubble div wrapping a rendered assistant message. */
function bubbleOf(text: string): HTMLElement {
  const el = screen.getByText(text)
  const bubble = el.closest('div[class*="rounded-2xl"]') as HTMLElement
  expect(bubble).toBeTruthy()
  return bubble
}

async function selectConv(title: string) {
  const item = screen.getAllByTestId('conv-item').find((el) => el.textContent?.includes(title))
  expect(item).toBeTruthy()
  await act(async () => {
    fireEvent.click(item!)
  })
}

async function openSettings() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
  })
  await act(async () => {
    fireEvent.click(screen.getByTestId('chat-header-settings'))
  })
  return screen.getByTestId('chat-settings-dialog')
}

async function renderPanel(props: { compact?: boolean } = {}) {
  render(<ChatPanel identity={null} {...props} />)
  // Boot auto-selects the server's first row (BUILD).
  await waitFor(() => expect(h.messages).toHaveBeenCalledWith('b-1'))
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  h.conversations.mockResolvedValue([BUILD, OPS])
  h.messages.mockImplementation(async (id: string) =>
    id === 'o-1'
      ? [{ id: 'm-1', role: 'assistant', content: 'ops reply', created_at: '2026-08-28T01:01:00Z' }]
      : [{ id: 'm-2', role: 'assistant', content: 'build reply', created_at: '2026-08-28T02:01:00Z' }],
  )
  h.context.mockResolvedValue(null)
  h.planTasks.mockResolvedValue({ tasks: [], created_at: null, turn_active: false })
  h.subscribeConvStateEvents.mockImplementation((() => () => {}) as never)
})

describe('019 chat tints', () => {
  it('ops chats default to the amber tint: pane wash + tinted plain bubbles', async () => {
    await renderPanel()
    // The building chat is untinted.
    expect(pane().className).not.toContain('bg-amber-500/15')
    await waitFor(() => expect(screen.getByText('build reply')).toBeTruthy())
    expect(bubbleOf('build reply').className).toContain('bg-ink-900/70')

    await selectConv('ops chat')
    await waitFor(() => expect(screen.getByText('ops reply')).toBeTruthy())
    expect(pane().className).toContain('bg-amber-500/15')
    const bubble = bubbleOf('ops reply')
    expect(bubble.className).toContain('bg-amber-950/45')
    expect(bubble.className).toContain('border-amber-500/15')
  })

  it('picking a tint in Chat settings applies immediately and persists', async () => {
    await renderPanel()
    await openSettings()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-tint-sky'))
    })
    expect(localStorage.getItem('luna.chat.tint.b-1')).toBe('sky')
    expect(pane().className).toContain('bg-sky-500/15')
    await waitFor(() => expect(screen.getByText('build reply')).toBeTruthy())
    expect(bubbleOf('build reply').className).toContain('bg-sky-950/45')
  })

  it('the Default swatch clears the tint — even the ops amber default', async () => {
    await renderPanel()
    await selectConv('ops chat')
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    expect(pane().className).toContain('bg-amber-500/15')
    await openSettings()
    // The effective tint (amber) shows as selected before any pick.
    expect(screen.getByTestId('chat-tint-amber').className).toContain('ring-2')
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-tint-none'))
    })
    expect(localStorage.getItem('luna.chat.tint.o-1')).toBe('none')
    expect(pane().className).not.toContain('bg-amber-500/15')
  })

  it('tints never touch user/reflection/automation bubbles', async () => {
    h.messages.mockImplementation(async () => [
      { id: 'm-u', role: 'user', content: 'me', created_at: '2026-08-28T01:01:00Z' },
      { id: 'm-r', role: 'assistant', content: 'a thought', source: 'curiosity', created_at: '2026-08-28T01:02:00Z' },
    ])
    localStorage.setItem('luna.chat.tint.b-1', 'rose')
    await renderPanel()
    await waitFor(() => expect(screen.getByText('me')).toBeTruthy())
    expect(bubbleOf('me').className).toContain('bg-luna-600')
    expect(bubbleOf('me').className).not.toContain('rose')
    expect(bubbleOf('a thought').className).toContain('bg-sky-950/40')
    expect(bubbleOf('a thought').className).not.toContain('rose')
  })
})

describe('019 chat settings dialog', () => {
  it('renames the chat: input + save calls the API and updates the header', async () => {
    await renderPanel()
    await openSettings()
    const input = screen.getByTestId('chat-settings-name') as HTMLInputElement
    expect(input.value).toBe('build chat')
    fireEvent.change(input, { target: { value: 'my project' } })
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-save-name'))
    })
    expect(h.renameConversation).toHaveBeenCalledWith('b-1', 'my project')
    expect(screen.getByTestId('chat-header-title').textContent).toBe('my project')
  })

  it('deletes through the settings dialog with the existing confirm step', async () => {
    await renderPanel()
    await openSettings()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-delete'))
    })
    expect(screen.getByTestId('delete-conversation-confirm')).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByTestId('delete-conversation-yes'))
    })
    expect(h.deleteConversation).toHaveBeenCalledWith('b-1')
    expect(screen.queryByTestId('chat-settings-dialog')).toBeNull()
  })
})

describe('019 header title + switcher', () => {
  it('titles the chat, not the agent, when the sidebar is visible', async () => {
    await renderPanel()
    expect(screen.getByTestId('chat-header-title').textContent).toBe('build chat')
    expect(screen.queryByTestId('chat-switcher-btn')).toBeNull()
  })

  it('compact (no list): the title is a pulldown that switches chats, ops pinned first', async () => {
    await renderPanel({ compact: true })
    expect(screen.queryByTestId('chat-header-title')).toBeNull()
    const btn = screen.getByTestId('chat-switcher-btn')
    expect(btn.textContent).toContain('build chat')
    await act(async () => {
      fireEvent.click(btn)
    })
    const items = screen.getAllByTestId('chat-switcher-item')
    expect(items.length).toBe(2)
    expect(items[0].textContent).toContain('ops chat') // pinned first
    expect(items[0].textContent).toContain('OPS')
    await act(async () => {
      fireEvent.click(items[0])
    })
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    expect(screen.queryByTestId('chat-switcher-menu')).toBeNull()
    expect(screen.getByTestId('chat-switcher-btn').textContent).toContain('ops chat')
  })
})
