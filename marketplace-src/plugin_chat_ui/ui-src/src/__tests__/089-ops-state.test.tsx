// @vitest-environment jsdom
// 089 — build/operate conversations. Ops conversations pin to the top of the
// sidebar with an amber OPS treatment and no delete affordance (the server
// 403s DELETE for them). The composer grows a state pulldown whose options
// depend on the conversation kind; changing it PATCHes the server, and a
// conversation.state_changed SSE event updates the local list live. The
// state is never flipped programmatically — only by the user's pick.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup, fireEvent } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import type { ConvStateEvent } from '../lib/convState'

const h = vi.hoisted(() => ({
  planTasks: vi.fn(),
  conversations: vi.fn(),
  messages: vi.fn(),
  context: vi.fn(async () => null),
  identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
  createConversation: vi.fn(),
  sendMessageStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
  deleteConversation: vi.fn(),
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
    renameConversation: vi.fn(),
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

// The plugin-local 089 module: keep the real helpers (STATE_OPTIONS, sorting,
// defaults) and stub only the two network edges (PATCH + SSE subscribe).
vi.mock('../lib/convState', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../lib/convState')>()
  return {
    ...orig,
    patchConversationState: h.patchConversationState,
    subscribeConvStateEvents: h.subscribeConvStateEvents,
  }
})

const BUILD = {
  id: 'b-1', title: 'build chat', kind: 'building', state: 'planning',
  created_at: '2026-08-28T02:00:00Z', updated_at: '2026-08-28T02:00:00Z',
}
const OPS = {
  id: 'o-1', title: 'ops chat', kind: 'ops', state: 'identify',
  created_at: '2026-08-28T01:00:00Z', updated_at: '2026-08-28T01:00:00Z',
}

async function selectConv(title: string) {
  const item = screen.getAllByTestId('conv-item').find((el) => el.textContent?.includes(title))
  expect(item).toBeTruthy()
  await act(async () => {
    fireEvent.click(item!)
  })
}

function statePulldown(): HTMLSelectElement {
  return screen.getByTestId('state-pulldown') as HTMLSelectElement
}

async function renderPanel() {
  render(<ChatPanel identity={null} />)
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
  h.conversations.mockResolvedValue([BUILD, OPS]) // server order: build first
  h.messages.mockResolvedValue([])
  h.context.mockResolvedValue(null)
  h.planTasks.mockResolvedValue({ tasks: [], created_at: null, turn_active: false })
  h.subscribeConvStateEvents.mockImplementation((() => () => {}) as never)
})

describe('089 ops conversations', () => {
  it('pins ops conversations to the top with an OPS tag and amber title', async () => {
    await renderPanel()
    const items = screen.getAllByTestId('conv-item')
    expect(items.length).toBe(2)
    // Server returned build first — the list still shows ops on top.
    expect(items[0].textContent).toContain('ops chat')
    expect(items[1].textContent).toContain('build chat')
    expect(items[0].className).toContain('text-amber-300')
    expect(items[0].querySelector('[data-testid="ops-tag"]')?.textContent).toBe('OPS')
    expect(items[1].querySelector('[data-testid="ops-tag"]')).toBeNull()
  })

  it('hides the delete affordance for ops conversations only', async () => {
    await renderPanel()
    // Active = building: delete is in the header menu.
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
    })
    expect(screen.getByTestId('chat-header-delete')).toBeTruthy()
    // Toggle it shut (fireEvent.click fires no mousedown, so the outside-click
    // closer never ran) before switching conversations.
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
    })

    await selectConv('ops chat')
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
    })
    expect(screen.getByTestId('chat-header-copy')).toBeTruthy() // menu IS open
    expect(screen.queryByTestId('chat-header-delete')).toBeNull()
  })
})

describe('089 state pulldown', () => {
  it('shows building states for building conversations', async () => {
    await renderPanel()
    const sel = statePulldown()
    expect(sel.value).toBe('planning') // the row's own state
    expect(Array.from(sel.options).map((o) => o.text)).toEqual(['Planning', 'Building'])
  })

  it('shows ops states for ops conversations', async () => {
    await renderPanel()
    await selectConv('ops chat')
    const sel = statePulldown()
    expect(sel.value).toBe('identify')
    expect(Array.from(sel.options).map((o) => o.text)).toEqual([
      'Identify', 'Fix & wait for approval', 'Fix & publish',
    ])
  })

  it('PATCHes the picked state and updates optimistically', async () => {
    await renderPanel()
    await act(async () => {
      fireEvent.change(statePulldown(), { target: { value: 'building' } })
    })
    expect(h.patchConversationState).toHaveBeenCalledWith('b-1', 'building')
    expect(statePulldown().value).toBe('building')

    await selectConv('ops chat')
    await act(async () => {
      fireEvent.change(statePulldown(), { target: { value: 'fix_publish' } })
    })
    expect(h.patchConversationState).toHaveBeenCalledWith('o-1', 'fix_publish')
    expect(statePulldown().value).toBe('fix_publish')
  })
})

describe('089 live state sync', () => {
  it('conversation.state_changed updates the list (and the open pulldown)', async () => {
    let broadcast: ((ev: ConvStateEvent) => void) | undefined
    h.subscribeConvStateEvents.mockImplementation(((cb: (ev: ConvStateEvent) => void) => {
      broadcast = cb
      return () => {}
    }) as never)
    await renderPanel()
    expect(broadcast).toBeTruthy()

    // The OPEN conversation's state changes elsewhere — the pulldown follows.
    await act(async () => {
      broadcast!({ conversation_id: 'b-1', kind: 'building', state: 'building' })
    })
    expect(statePulldown().value).toBe('building')
    expect(h.patchConversationState).not.toHaveBeenCalled() // mirror, not a write

    // A BACKGROUND conversation's state changes — its row is patched too.
    await act(async () => {
      broadcast!({ conversation_id: 'o-1', kind: 'ops', state: 'fix_approve' })
    })
    await selectConv('ops chat')
    expect(statePulldown().value).toBe('fix_approve')
  })
})
