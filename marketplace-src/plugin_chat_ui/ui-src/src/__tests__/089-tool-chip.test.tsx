// @vitest-environment jsdom
// 089 — the wrench progress chip. In-turn tool activity paints as ONE
// fixed-height chip (spinner + 🔧 + running count) instead of a growing
// stream of receipt rows; clicking it toggles a bounded detail panel. When
// the turn ends the chip settles to "🔧 n tools" (rose accent if any call
// errored) and survives until the NEXT turn starts fresh.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup, fireEvent } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'
import { CHAT_BRIDGE_EVENT } from '@luna/lib/pluginBridge'

type StreamCbs = {
  onDelta?: (d: string) => void
  onToolCall?: (names: string[]) => void
  onToolResult?: (name: string, result: string, embed?: Record<string, string>) => void
  onDone?: (text: string, meta?: Record<string, unknown>) => void
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

// Keep the 089 SSE module quiet in this suite (no real network).
vi.mock('../lib/convState', async (importOriginal) => {
  const orig = await importOriginal<typeof import('../lib/convState')>()
  return {
    ...orig,
    patchConversationState: vi.fn(async () => {}),
    subscribeConvStateEvents: vi.fn(() => () => {}),
  }
})

const C1 = { id: 'c1', title: 'first chat', created_at: '2026-08-28T00:00:00Z', updated_at: '2026-08-28T00:00:00Z' }

function bridgeSend(text: string) {
  window.dispatchEvent(new CustomEvent(CHAT_BRIDGE_EVENT, { detail: { action: 'send', text } }))
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
})

/** Render, start a turn in c1, return its stream callbacks + an end-turn knob. */
async function startTurn(): Promise<{ cbs: StreamCbs; endTurn: () => void }> {
  let cbs: StreamCbs = {}
  let endTurn: () => void = () => {}
  h.sendMessageStream.mockImplementation((_conv: string, _text: string, callbacks: StreamCbs) => {
    cbs = callbacks
    return new Promise<void>((resolve) => { endTurn = () => resolve() })
  })
  render(<ChatPanel identity={null} />)
  await waitFor(() => expect(h.messages).toHaveBeenCalledWith('c1'))
  await act(async () => {
    bridgeSend('do things')
  })
  await waitFor(() => expect(h.sendMessageStream).toHaveBeenCalled())
  return { cbs, endTurn }
}

describe('089 tool progress chip', () => {
  it('one fixed-height chip counts tool calls — the timeline never grows per call', async () => {
    const { cbs } = await startTurn()
    expect(screen.queryByTestId('tool-progress-chip')).toBeNull() // no tools yet
    await act(async () => {
      cbs.onToolCall?.(['files.read', 'shell.run'])
    })
    const chip = screen.getByTestId('tool-progress-chip')
    expect(screen.getByTestId('tool-progress-count').textContent).toBe('2')
    expect(chip.className).toContain('h-7') // fixed height
    await act(async () => {
      cbs.onToolCall?.(['web.search'])
      cbs.onToolCall?.(['files.write'])
    })
    // Still exactly one chip, same fixed height, only the count moved.
    expect(screen.getAllByTestId('tool-progress-chip').length).toBe(1)
    expect(screen.getByTestId('tool-progress-count').textContent).toBe('4')
    expect(screen.getByTestId('tool-progress-chip').className).toContain('h-7')
    // No per-call receipt rows appear during the turn.
    expect(screen.queryByTestId('auto-tool-receipts')).toBeNull()
  })

  it('click toggles the expanded panel listing every call', async () => {
    const { cbs } = await startTurn()
    await act(async () => {
      cbs.onToolCall?.(['files.read', 'shell.run'])
      cbs.onToolResult?.('files.read', 'file contents')
    })
    expect(screen.queryByTestId('tool-progress-panel')).toBeNull()
    await act(async () => {
      fireEvent.click(screen.getByTestId('tool-progress-chip'))
    })
    const panel = screen.getByTestId('tool-progress-panel')
    expect(panel.textContent).toContain('files.read')
    expect(panel.textContent).toContain('shell.run')
    await act(async () => {
      fireEvent.click(screen.getByTestId('tool-progress-chip'))
    })
    expect(screen.queryByTestId('tool-progress-panel')).toBeNull()
  })

  it('collapses to the final count when the turn ends — and survives it', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onToolCall?.(['files.read', 'shell.run', 'web.search'])
      cbs.onToolResult?.('files.read', 'ok')
      cbs.onToolResult?.('shell.run', 'ok')
      cbs.onToolResult?.('web.search', 'ok')
      cbs.onDone?.('all done', {})
      endTurn()
    })
    await waitFor(() =>
      expect(screen.getByTestId('tool-progress-count').textContent).toBe('3 tools'),
    )
    expect(screen.getByTestId('tool-progress-chip').className).not.toContain('rose')
  })

  it('carries an error accent when any tool errored', async () => {
    const { cbs, endTurn } = await startTurn()
    await act(async () => {
      cbs.onToolCall?.(['shell.run'])
      cbs.onToolResult?.('shell.run', 'Error: command not found')
      cbs.onDone?.('done', {})
      endTurn()
    })
    await waitFor(() =>
      expect(screen.getByTestId('tool-progress-count').textContent).toBe('1 tool'),
    )
    expect(screen.getByTestId('tool-progress-chip').className).toContain('rose')
  })
})
