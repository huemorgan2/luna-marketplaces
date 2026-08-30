// @vitest-environment jsdom
// 089/014 — build/operate conversations. Ops conversations pin to the top of
// the sidebar with an amber OPS treatment and no delete affordance (the
// server 403s DELETE for them). The composer carries the agent-state picker
// INSIDE the message box (014): a custom upward menu (eyebrow header, bold
// label + one explanation line per option) whose options depend on the
// conversation kind; picking one PATCHes the server, and a
// conversation.state_changed SSE event updates the local list live. The
// state is never flipped programmatically — only by the user's pick. Ops
// chats also get an amber capability line next to the model selector with a
// hover tooltip.
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

  it('lists the ops states with their explanations for ops conversations', async () => {
    await renderPanel()
    await selectConv('ops chat')
    expect(stateTrigger().textContent).toContain('Identify')
    await openStateMenu()
    expect(screen.getByTestId('state-option-identify').textContent)
      .toContain('Diagnose only — report problems, change nothing.')
    expect(screen.getByTestId('state-option-fix_approve').textContent)
      .toContain('Prepare fixes; each waits for your approval.')
    expect(screen.getByTestId('state-option-fix_publish').textContent)
      .toContain('Fix and publish without waiting.')
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

    await selectConv('ops chat')
    await pickState('fix_publish')
    expect(h.patchConversationState).toHaveBeenCalledWith('o-1', 'fix_publish')
    expect(stateTrigger().textContent).toContain('Fix & publish')
  })

  it('closed chip has no amber border — amber only while the menu is open (ops)', async () => {
    await renderPanel()
    await selectConv('ops chat')
    const closed = stateTrigger()
    expect(closed.className).toContain('border-white/10') // hairline neutral
    expect(closed.className).not.toContain('border-amber')
    expect(closed.className).toContain('text-amber-300') // amber TEXT stays
    await openStateMenu()
    expect(stateTrigger().className).toContain('border-amber-500/60')
    await act(async () => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    expect(stateTrigger().className).not.toContain('border-amber')
  })
})

describe('014 ops capability line', () => {
  it('shows the per-state amber capability text — ops chats only', async () => {
    await renderPanel()
    // Building chat: no capability line.
    expect(screen.queryByTestId('ops-capability-line')).toBeNull()

    await selectConv('ops chat')
    expect(screen.getByTestId('ops-capability-line').textContent)
      .toContain('diagnose only — no changes')
    await pickState('fix_approve')
    expect(screen.getByTestId('ops-capability-line').textContent)
      .toContain('fixes wait for your approval')
    await pickState('fix_publish')
    expect(screen.getByTestId('ops-capability-line').textContent)
      .toContain('fixes publish without approval')
  })

  it('shows the explanation tooltip on hover, hides it on leave', async () => {
    await renderPanel()
    await selectConv('ops chat')
    expect(screen.queryByTestId('ops-capability-tooltip')).toBeNull()
    await act(async () => {
      fireEvent.mouseEnter(screen.getByTestId('ops-capability-line'))
    })
    const tip = screen.getByTestId('ops-capability-tooltip')
    expect(tip.textContent).toContain('Operations chat')
    expect(tip.textContent).toContain('the state only controls what the agent may change')
    await act(async () => {
      fireEvent.mouseLeave(screen.getByTestId('ops-capability-line'))
    })
    expect(screen.queryByTestId('ops-capability-tooltip')).toBeNull()
  })
})

describe('089 live state sync', () => {
  it('conversation.state_changed updates the list (and the open picker + capability line)', async () => {
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

    // A BACKGROUND conversation's state changes — its row is patched too, and
    // the ops capability line reflects it once that chat is opened.
    await act(async () => {
      broadcast!({ conversation_id: 'o-1', kind: 'ops', state: 'fix_approve' })
    })
    await selectConv('ops chat')
    expect(stateTrigger().textContent).toContain('Fix & wait for approval')
    expect(screen.getByTestId('ops-capability-line').textContent)
      .toContain('fixes wait for your approval')
  })
})
