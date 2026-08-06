// @vitest-environment jsdom
// 069 — the `wait` tool countdown: ui.wait.* frames render one shimmer line
// ("waiting Ns — reason"), server ticks resync it, `finished` flips it to
// "resuming…", and the next visible stream activity clears it.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'

type StreamHandlers = {
  onDelta: (d: string) => void
  onDone: (t: string, m?: unknown) => void
  onUiEvent?: (evt: { type: string; [key: string]: unknown }) => void
}

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

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

let handlers: StreamHandlers | null = null
let finishTurn: (() => void) | null = null

beforeEach(() => {
  cleanup()
  localStorage.clear()
  vi.clearAllMocks()
  handlers = null
  h.identity.mockResolvedValue({ name: 'Luna', emoji: '🌙' })
  h.conversations.mockResolvedValue([])
  h.messages.mockResolvedValue([])
  h.createConversation.mockResolvedValue({
    id: 'c1', title: null,
    created_at: '2026-08-07T00:00:00Z', updated_at: '2026-08-07T00:00:00Z',
  })
  h.sendMessageStream.mockImplementation(
    (_id: string, _text: string, hs: StreamHandlers) =>
      new Promise<void>((resolve) => {
        handlers = hs
        finishTurn = () => {
          hs.onDone('', undefined)
          resolve()
        }
      }),
  )
})

async function startTurn() {
  render(<ChatPanel identity={null} />)
  const box = (await screen.findByPlaceholderText(/Message /)) as HTMLTextAreaElement
  fireEvent.change(box, { target: { value: 'deploy and verify' } })
  fireEvent.keyDown(box, { key: 'Enter', code: 'Enter' })
  await waitFor(() => expect(handlers).not.toBeNull())
}

describe('069 wait countdown', () => {
  it('renders the shimmer line with seconds + reason, resyncs on tick', async () => {
    await startTurn()
    handlers!.onUiEvent?.({ type: 'ui.wait.started', seconds: 30, reason: 'deploy finishing' })
    const line = await screen.findByTestId('wait-line')
    expect(line.textContent).toMatch(/waiting \d+s — deploy finishing/)
    handlers!.onUiEvent?.({ type: 'ui.wait.tick', remaining: 10, seconds: 30, reason: 'deploy finishing' })
    await waitFor(() => {
      const m = /waiting (\d+)s/.exec(screen.getByTestId('wait-line').textContent || '')
      expect(m && Number(m[1])).toBeLessThanOrEqual(11)
    })
  })

  it('flips to resuming on finished, clears on the next delta', async () => {
    await startTurn()
    handlers!.onUiEvent?.({ type: 'ui.wait.started', seconds: 15, reason: 'ci' })
    await screen.findByTestId('wait-line')
    handlers!.onUiEvent?.({ type: 'ui.wait.finished', seconds: 15, reason: 'ci' })
    await waitFor(() =>
      expect(screen.getByTestId('wait-line').textContent).toContain('resuming…'),
    )
    handlers!.onDelta('Deploy is done — verified.')
    await waitFor(() => expect(screen.queryByTestId('wait-line')).toBeNull())
  })

  it('clears when the turn ends even without a delta', async () => {
    await startTurn()
    handlers!.onUiEvent?.({ type: 'ui.wait.started', seconds: 20, reason: 'job' })
    await screen.findByTestId('wait-line')
    finishTurn!()
    await waitFor(() => expect(screen.queryByTestId('wait-line')).toBeNull())
  })
})
