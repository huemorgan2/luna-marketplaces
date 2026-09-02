// @vitest-environment jsdom
// 089/014/098/019 — build/operate conversations. Ops conversations pin to
// the top of the sidebar but look like every other row (019: the OPS chip is
// the only marker; the amber moved into the chat area's tint) and have no
// delete affordance (the server 403s DELETE for them; delete lives in the
// Chat settings dialog now). The composer carries the agent-state
// picker INSIDE the message box (014) for BUILDING chats only: a custom
// upward menu (eyebrow header, bold label + one explanation line per option);
// picking one PATCHes the server, and a conversation.state_changed SSE event
// updates the local list live. The state is never flipped programmatically —
// only by the user's pick. luna 098: ops chats have no modes — no picker, no
// capability line; legacy mode states on ops rows are healed to 'building'.
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

/** 014: the state picker trigger chip (a button inside the composer box). */
function stateTrigger(): HTMLButtonElement {
  return screen.getByTestId('state-pulldown') as HTMLButtonElement
}

async function openStateMenu(): Promise<HTMLElement> {
  await act(async () => {
    fireEvent.click(stateTrigger())
  })
  return screen.getByTestId('state-menu')
}

/** Open the menu and click the row for `value`. */
async function pickState(value: string) {
  await openStateMenu()
  await act(async () => {
    fireEvent.click(screen.getByTestId(`state-option-${value}`))
  })
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
  it('pins ops conversations to the top with an OPS tag', async () => {
    await renderPanel()
    const items = screen.getAllByTestId('conv-item')
    expect(items.length).toBe(2)
    // Server returned build first — the list still shows ops on top.
    expect(items[0].textContent).toContain('ops chat')
    expect(items[1].textContent).toContain('build chat')
    expect(items[0].querySelector('[data-testid="ops-tag"]')?.textContent).toBe('OPS')
    expect(items[1].querySelector('[data-testid="ops-tag"]')).toBeNull()
  })

  it('019: the ops row is styled exactly like every other row — no amber', async () => {
    await renderPanel()
    // Boot selected the build chat — both rows inactive/active generic.
    let items = screen.getAllByTestId('conv-item')
    let opsRow = items.find((el) => el.textContent?.includes('ops chat'))!
    expect(opsRow.className).not.toContain('amber')
    expect(opsRow.className).toContain('text-ink-300')
    expect(opsRow.className).toContain('border-transparent')

    await selectConv('ops chat')
    items = screen.getAllByTestId('conv-item')
    opsRow = items.find((el) => el.textContent?.includes('ops chat'))!
    const buildRow = items.find((el) => el.textContent?.includes('build chat'))!
    // Selected ops = the same luna treatment a selected build row gets.
    expect(opsRow.className).toContain('bg-luna-600/20')
    expect(opsRow.className).not.toContain('amber')
    expect(buildRow.className).toContain('text-ink-300')
    // The OPS chip itself keeps its amber identity.
    expect(opsRow.querySelector('[data-testid="ops-tag"]')).toBeTruthy()
  })

  it('019: delete lives in Chat settings and is absent for ops conversations', async () => {
    await renderPanel()
    // Active = building: the menu offers Chat settings (no direct rename/delete).
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
    })
    expect(screen.queryByTestId('chat-header-rename')).toBeNull()
    expect(screen.queryByTestId('chat-header-delete')).toBeNull()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-settings'))
    })
    expect(screen.getByTestId('chat-settings-delete')).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-settings-close'))
    })

    await selectConv('ops chat')
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-menu-btn'))
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId('chat-header-settings'))
    })
    expect(screen.getByTestId('chat-settings-dialog')).toBeTruthy() // dialog IS open
    expect(screen.queryByTestId('chat-settings-delete')).toBeNull()
  })
})

describe('014 state picker (in-composer dropdown)', () => {
  it('sits inside the composer box, on the attach/Send row', async () => {
    await renderPanel()
    const trigger = stateTrigger()
    // Same row as the attach clip; that row lives inside the rounded box.
    const row = trigger.parentElement?.parentElement as HTMLElement
    expect(row.contains(screen.getByTestId('attach-button'))).toBe(true)
    // No native <select> anywhere anymore.
    expect(document.querySelector('select[data-testid="state-pulldown"]')).toBeNull()
  })

  it('opens an upward menu with eyebrow header and one explanation line per building state', async () => {
    await renderPanel()
    expect(stateTrigger().textContent).toContain('Planning') // the row's own state
    expect(screen.queryByTestId('state-menu')).toBeNull()
    const menu = await openStateMenu()
    expect(menu.textContent).toContain('AGENT STATE')
    expect(screen.getByTestId('state-option-planning').textContent)
      .toContain('Think and plan — research and notes, no building yet.')
    expect(screen.getByTestId('state-option-building').textContent)
      .toContain('Full build — create and change playbooks, schedules, files.')
    // Current option is highlighted.
    expect(screen.getByTestId('state-option-planning').closest('[role="option"]')
      ?.getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('state-option-building').closest('[role="option"]')
      ?.getAttribute('aria-selected')).toBe('false')
  })

  it('098: ops conversations have no state picker at all', async () => {
    await renderPanel()
    expect(screen.getByTestId('state-pulldown')).toBeTruthy() // building chat has it
    await selectConv('ops chat')
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    expect(screen.queryByTestId('state-pulldown')).toBeNull()
    expect(screen.queryByTestId('state-details-toggle')).toBeNull()
  })

  it('closes on Escape and on outside click without writing', async () => {
    await renderPanel()
    await openStateMenu()
    await act(async () => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    expect(screen.queryByTestId('state-menu')).toBeNull()
    await openStateMenu()
    await act(async () => {
      fireEvent.mouseDown(document.body)
    })
    expect(screen.queryByTestId('state-menu')).toBeNull()
    expect(h.patchConversationState).not.toHaveBeenCalled()
  })

  it('PATCHes the picked state, updates optimistically, and closes the menu', async () => {
    await renderPanel()
    await pickState('building')
    expect(h.patchConversationState).toHaveBeenCalledWith('b-1', 'building')
    expect(stateTrigger().textContent).toContain('Building')
    expect(screen.queryByTestId('state-menu')).toBeNull()
  })

  it('098: no capability line anywhere — the bottom state label is gone', async () => {
    await renderPanel()
    expect(screen.queryByTestId('ops-capability-line')).toBeNull()
    await selectConv('ops chat')
    await waitFor(() => expect(h.messages).toHaveBeenCalledWith('o-1'))
    expect(screen.queryByTestId('ops-capability-line')).toBeNull()
    expect(screen.queryByTestId('ops-capability-tooltip')).toBeNull()
  })
})

describe('089 live state sync', () => {
  it('conversation.state_changed updates the list (and the open picker)', async () => {
    let broadcast: ((ev: ConvStateEvent) => void) | undefined
    h.subscribeConvStateEvents.mockImplementation(((cb: (ev: ConvStateEvent) => void) => {
      broadcast = cb
      return () => {}
    }) as never)
    await renderPanel()
    expect(broadcast).toBeTruthy()

    // The OPEN conversation's state changes elsewhere — the picker follows.
    await act(async () => {
      broadcast!({ conversation_id: 'b-1', kind: 'building', state: 'building' })
    })
    expect(stateTrigger().textContent).toContain('Building')
    expect(h.patchConversationState).not.toHaveBeenCalled() // mirror, not a write

    // 098: a stray event for the ops chat changes nothing visible — ops chats
    // render no state UI regardless of the stored value. 021: and since the
    // raw state is no longer 'identify', the notice box withdraws too.
    await act(async () => {
      broadcast!({ conversation_id: 'o-1', kind: 'ops', state: 'fix_approve' })
    })
    await selectConv('ops chat')
    expect(screen.queryByTestId('state-pulldown')).toBeNull()
    expect(screen.queryByTestId('ops-capability-line')).toBeNull()
    expect(screen.queryByTestId('ops-identify-notice')).toBeNull()
  })
})

// 021 (luna 099): the ops chat is identify-only — it finds and reports
// issues, never fixes. The standing notice under the header states that
// contract, and keys on the RAW server state so it never promises restraint
// a different core version does not enforce.
describe('021 ops identify notice', () => {
  it('shows the standing notice at the top of the ops chat — and only there', async () => {
    await renderPanel()
    expect(screen.queryByTestId('ops-identify-notice')).toBeNull() // building chat
    await selectConv('ops chat')
    const box = screen.getByTestId('ops-identify-notice')
    expect(box.textContent).toContain('Finding issues & live activity only.')
    expect(box.textContent).toContain('take the finding to a regular chat')
  })

  it('keys on the RAW server state — no notice when the core runs ops in another state', async () => {
    h.conversations.mockResolvedValue([BUILD, { ...OPS, state: 'building' }])
    await renderPanel()
    await selectConv('ops chat')
    expect(screen.queryByTestId('ops-identify-notice')).toBeNull()
  })
})
