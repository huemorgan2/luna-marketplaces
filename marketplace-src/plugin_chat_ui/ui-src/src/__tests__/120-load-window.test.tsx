// @vitest-environment jsdom
// 120 (plan 027) — bounded conversation load. A conversation that FAILS to
// load must never be painted as an empty chat: the pane shows a load-error
// card with a reason and a Retry that re-runs the same load. The load is a
// window (newest 200 rows); when the server says more rows exist, a "Load
// earlier messages" control prepends the next page and disappears once the
// server reports has-more false. Truncated rows grow a "Show full message"
// link that swaps in the untruncated single row.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, act, cleanup, fireEvent } from '@testing-library/react'
import { ChatPanel } from '../views/ChatPanel'

const h = vi.hoisted(() => ({
  conversations: vi.fn(),
  messages: vi.fn(),
  messagesPage: vi.fn(),
  message: vi.fn(),
  context: vi.fn(async () => null),
  identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
}))

vi.mock('@luna/lib/api', () => ({
  api: {
    planTasks: vi.fn(async () => ({ tasks: [], created_at: null, turn_active: false })),
    conversations: h.conversations,
    messages: h.messages,
    messagesPage: h.messagesPage,
    message: h.message,
    context: h.context,
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
  cardAction: vi.fn(),
  uploadAttachment: vi.fn(),
  sendMessageStream: vi.fn(),
  continueConversation: vi.fn(async () => {}),
  startOnboardingStream: vi.fn(),
  subscribeApprovalEvents: vi.fn(() => () => {}),
  queueMessage: vi.fn(),
  stopTurn: vi.fn(),
  getTurnStatus: vi.fn(async () => ({ active: false })),
  attachTurnStream: vi.fn(),
}))

const C1 = { id: 'c1', title: 'long chat', created_at: '2026-09-20T00:00:00Z', updated_at: '2026-09-27T00:00:00Z' }
const C2 = { id: 'c2', title: 'other chat', created_at: '2026-09-20T01:00:00Z', updated_at: '2026-09-27T01:00:00Z' }

const row = (id: string, content: string, t: string, extra: Record<string, unknown> = {}) => ({
  id, role: 'user', content, created_at: t, ...extra,
})

function httpError(status: number): Error {
  const e = new Error(`${status} Bad Gateway`) as Error & { httpStatus?: number }
  e.httpStatus = status
  return e
}

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const page = (rows: unknown[], hasMore = false, nextBefore: string | null = null, total = rows.length) =>
  ({ rows, hasMore, nextBefore, total })

async function selectConv(title: string) {
  const item = screen.getAllByTestId('conv-item').find((el) => el.textContent?.includes(title))
  expect(item).toBeTruthy()
  await act(async () => { fireEvent.click(item!) })
}

const tick = () => new Promise((r) => setTimeout(r, 20))

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
})

describe('120 bounded load window', () => {
  it('a failed load shows the error card with Retry — never the greeting; retry renders the rows', async () => {
    h.messagesPage.mockRejectedValue(httpError(502))
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(h.messagesPage).toHaveBeenCalled())
    // The load request is the 200-row window with an abort signal.
    expect(h.messagesPage.mock.calls[0][0]).toBe('c1')
    expect(h.messagesPage.mock.calls[0][1]).toEqual(expect.objectContaining({ limit: 200 }))
    expect(h.messagesPage.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal)

    const card = await screen.findByTestId('load-error-card')
    expect(card.textContent).toContain("Couldn't load this conversation.")
    expect(screen.getByTestId('load-error-reason').textContent).toBe('HTTP 502')
    expect(screen.queryByText(/Hi, I'm/)).toBeNull()
    expect(screen.queryByText('Loading conversation…')).toBeNull()

    h.messagesPage.mockResolvedValue({
      rows: [row('m1', 'the real first row', '2026-09-21T00:00:00Z')],
      hasMore: false,
      nextBefore: null,
      total: 1,
    })
    await act(async () => {
      fireEvent.click(screen.getByText('Retry'))
    })
    await screen.findByText('the real first row')
    expect(screen.queryByTestId('load-error-card')).toBeNull()
    expect(screen.queryByText(/Hi, I'm/)).toBeNull()
  })

  it('a timeout reads as "timed out after 60 s" and keeps the composer draft', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      localStorage.setItem('luna.draft.c1', 'unsent words')
      h.messagesPage.mockReturnValue(new Promise(() => {})) // never settles
      render(<ChatPanel identity={null} />)
      await waitFor(() => expect(h.messagesPage).toHaveBeenCalled())
      await act(async () => {
        vi.advanceTimersByTime(60_001)
      })
      const card = await screen.findByTestId('load-error-card')
      expect(card.textContent).toContain('timed out after 60 s')
      expect(screen.queryByText(/Hi, I'm/)).toBeNull()
      const box = screen.getByPlaceholderText(/Message /) as HTMLTextAreaElement
      expect(box.value).toBe('unsent words')
    } finally {
      vi.useRealTimers()
    }
  })

  it('has-more renders "Load earlier messages"; clicking prepends the older page and hides the button when done', async () => {
    h.messagesPage.mockResolvedValueOnce({
      rows: [row('m2', 'second row', '2026-09-22T00:00:00Z'), row('m3', 'third row', '2026-09-23T00:00:00Z')],
      hasMore: true,
      nextBefore: 'm2',
      total: 3,
    })
    render(<ChatPanel identity={null} />)
    await screen.findByText('third row')
    const btn = (await screen.findByText(/Load earlier messages/)).closest('button')!
    expect(btn.textContent).toContain('(1 more)')
    expect(screen.queryByText(/Hi, I'm/)).toBeNull()

    h.messagesPage.mockResolvedValueOnce({
      rows: [row('m1', 'first row', '2026-09-21T00:00:00Z')],
      hasMore: false,
      nextBefore: null,
      total: 3,
    })
    await act(async () => {
      fireEvent.click(btn)
    })
    await screen.findByText('first row')
    expect(h.messagesPage).toHaveBeenLastCalledWith('c1', expect.objectContaining({ limit: 200, before: 'm2' }))
    // Older rows sit in FRONT of the window that was already on screen.
    const first = screen.getByText('first row')
    const second = screen.getByText('second row')
    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('second row')).toBeTruthy()
    expect(screen.getByText('third row')).toBeTruthy()
    await waitFor(() => expect(screen.queryByTestId('load-earlier')).toBeNull())
  })

  it('a failed "Load earlier" shows its error under the button and keeps the loaded rows', async () => {
    h.messagesPage.mockResolvedValueOnce({
      rows: [row('m2', 'second row', '2026-09-22T00:00:00Z')],
      hasMore: true,
      nextBefore: 'm2',
      total: 2,
    })
    render(<ChatPanel identity={null} />)
    await screen.findByText('second row')
    const btn = (await screen.findByText(/Load earlier messages/)).closest('button')!
    h.messagesPage.mockRejectedValueOnce(httpError(503))
    await act(async () => {
      fireEvent.click(btn)
    })
    const err = await screen.findByTestId('load-earlier-error')
    expect(err.textContent).toContain('HTTP 503')
    expect(screen.getByText('second row')).toBeTruthy()
    expect(screen.getByTestId('load-earlier')).toBeTruthy()
  })

  it('a truncated row offers "Show full message" and swaps in the full row', async () => {
    h.messagesPage.mockResolvedValueOnce({
      rows: [row('a1', 'cut text\n… [truncated]', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: true })],
      hasMore: false,
      nextBefore: null,
      total: 1,
    })
    h.message.mockResolvedValue(row('a1', 'the whole untruncated text', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: null }))
    render(<ChatPanel identity={null} />)
    const link = await screen.findByText('Show full message')
    await act(async () => {
      fireEvent.click(link)
    })
    await screen.findByText('the whole untruncated text')
    expect(h.message).toHaveBeenCalledWith('c1', 'a1')
    expect(screen.queryByText('Show full message')).toBeNull()
  })

  it('a failed "Show full message" keeps the cut text and says so inline', async () => {
    h.messagesPage.mockResolvedValueOnce({
      rows: [row('a1', 'cut text here', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: true })],
      hasMore: false,
      nextBefore: null,
      total: 1,
    })
    h.message.mockRejectedValue(httpError(404))
    render(<ChatPanel identity={null} />)
    const link = await screen.findByText('Show full message')
    await act(async () => {
      fireEvent.click(link)
    })
    const err = await screen.findByTestId('truncated-row-error')
    expect(err.textContent).toContain('404')
    expect(screen.getByText('cut text here')).toBeTruthy()
    expect(screen.getByText('Show full message')).toBeTruthy()
  })
})

// Review fixes on 0.31.0: state that must stay PER conversation (012), the
// deep-link path, the resync merge's tie handling and Copy on a cut row.
describe('120 review: per-conversation isolation and deep links', () => {
  it('a superseded load that fails AFTER a newer load of the same conversation succeeded paints no error card', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    const first = deferred<unknown>()
    h.messagesPage.mockImplementationOnce(() => first.promise) // c1 #1 hangs
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(h.messagesPage).toHaveBeenCalledTimes(1))
    h.messagesPage.mockResolvedValueOnce(page([row('b1', 'c2 row', '2026-09-22T00:00:00Z')]))
    await selectConv('other chat')
    await screen.findByText('c2 row')
    h.messagesPage.mockResolvedValueOnce(page([row('a1', 'c1 row', '2026-09-22T00:00:00Z')]))
    await selectConv('long chat')
    await screen.findByText('c1 row')
    expect(screen.queryByTestId('load-error-card')).toBeNull()
    // The FIRST c1 request now rejects (slow proxy 502 / the 60 s abort).
    await act(async () => { first.reject(httpError(502)) })
    await act(tick)
    expect(screen.queryByTestId('load-error-card')).toBeNull()
    expect(screen.getByText('c1 row')).toBeTruthy()
    expect(screen.queryByText('Loading conversation…')).toBeNull()
  })

  it('"Load earlier" state stays with its conversation: B is not disabled by A\'s page and never shows A\'s failure', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    h.messagesPage.mockResolvedValueOnce(page([row('a2', 'c1 row2', '2026-09-22T00:00:00Z')], true, 'a2', 2))
    render(<ChatPanel identity={null} />)
    await screen.findByText('c1 row2')
    const btnA = (await screen.findByText(/Load earlier messages/)).closest('button')!
    const earlier = deferred<unknown>()
    h.messagesPage.mockImplementationOnce(() => earlier.promise) // c1's older page hangs
    await act(async () => { fireEvent.click(btnA) })
    expect(btnA.disabled).toBe(true)

    h.messagesPage.mockResolvedValueOnce(page([row('b2', 'c2 row2', '2026-09-22T00:00:00Z')], true, 'b2', 5))
    await selectConv('other chat')
    await screen.findByText('c2 row2')
    const btnB = (await screen.findByText(/Load earlier messages/)).closest('button')!
    expect(btnB.disabled).toBe(false)
    expect(btnB.textContent).toContain('(4 more)')

    await act(async () => { earlier.reject(httpError(503)) })
    await act(tick)
    expect(screen.queryByTestId('load-earlier-error')).toBeNull()
    expect(screen.getByText('c2 row2')).toBeTruthy()

    // Back on A the failure is reported where it happened.
    h.messagesPage.mockResolvedValueOnce(page([row('a2', 'c1 row2', '2026-09-22T00:00:00Z')], true, 'a2', 2))
    await selectConv('long chat')
    await screen.findByText('c1 row2')
    // A re-select clears A's stale page error too (the window was refetched).
    expect(screen.queryByTestId('load-earlier-error')).toBeNull()
    expect((screen.getByText(/Load earlier messages/).closest('button') as HTMLButtonElement).disabled).toBe(false)
  })

  it('"Load earlier" failure is reported on the conversation it belongs to when the user stays there', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    h.messagesPage.mockResolvedValueOnce(page([row('a2', 'c1 row2', '2026-09-22T00:00:00Z')], true, 'a2', 2))
    render(<ChatPanel identity={null} />)
    await screen.findByText('c1 row2')
    const btnA = (await screen.findByText(/Load earlier messages/)).closest('button')!
    h.messagesPage.mockRejectedValueOnce(httpError(503))
    await act(async () => { fireEvent.click(btnA) })
    expect((await screen.findByTestId('load-earlier-error')).textContent).toContain('HTTP 503')
    // Switching to B hides it; B has no error of its own.
    h.messagesPage.mockResolvedValueOnce(page([row('b2', 'c2 row2', '2026-09-22T00:00:00Z')], true, 'b2', 5))
    await selectConv('other chat')
    await screen.findByText('c2 row2')
    expect(screen.queryByTestId('load-earlier-error')).toBeNull()
  })

  it('a deep-linked conversation that 502s keeps its error card — no fallback to the newest conversation', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    h.messagesPage.mockImplementation(async (id: string) => {
      if (id === 'c2') throw httpError(502)
      return page([row('a1', 'c1 row', '2026-09-22T00:00:00Z')])
    })
    render(<ChatPanel identity={null} initialConversationId="c2" />)
    const card = await screen.findByTestId('load-error-card')
    expect(card.textContent).toContain('HTTP 502')
    await act(tick)
    expect(screen.getByTestId('load-error-card')).toBeTruthy()
    expect(screen.queryByText('c1 row')).toBeNull()
    expect(screen.queryByText(/Hi, I'm/)).toBeNull()
    expect(h.messagesPage.mock.calls.map((c) => c[0])).toEqual(['c2'])
  })

  it('a deep-linked conversation that does not exist (404) still falls back to the newest one', async () => {
    h.conversations.mockResolvedValue([C1, C2])
    h.messagesPage.mockImplementation(async (id: string) => {
      if (id === 'gone') throw httpError(404)
      return page([row('a1', 'c1 row', '2026-09-22T00:00:00Z')])
    })
    render(<ChatPanel identity={null} initialConversationId="gone" />)
    await screen.findByText('c1 row')
    expect(screen.queryByTestId('load-error-card')).toBeNull()
    expect(h.messagesPage.mock.calls.map((c) => c[0])).toEqual(['gone', 'c1'])
  })

  it('Copy on a truncated bubble copies the full row, not the cut text', async () => {
    h.messagesPage.mockResolvedValueOnce(page([
      row('a1', 'cut text\n… [truncated]', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: true }),
    ]))
    h.message.mockResolvedValue(row('a1', 'the whole untruncated text', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: null }))
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<ChatPanel identity={null} />)
    await screen.findByText('Show full message')
    fireEvent.click(screen.getByTestId('message-menu-btn'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('message-menu-copy'))
    })
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('the whole untruncated text'))
    expect(h.message).toHaveBeenCalledWith('c1', 'a1')
  })

  it('Copy on a truncated bubble falls back to the cut text when the full row cannot be fetched', async () => {
    h.messagesPage.mockResolvedValueOnce(page([
      row('a1', 'cut text here', '2026-09-22T00:00:00Z', { role: 'assistant', truncated: true }),
    ]))
    h.message.mockRejectedValue(httpError(503))
    const writeText = vi.fn(async () => {})
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    render(<ChatPanel identity={null} />)
    await screen.findByText('Show full message')
    fireEvent.click(screen.getByTestId('message-menu-btn'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('message-menu-copy'))
    })
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('cut text here'))
  })
})

describe('120 mergeWindow (resync keeps paged-in rows)', () => {
  const m = (id: string, t: string) => ({ id, role: 'user' as const, content: id, created_at: t })

  it('keeps rows in front of the fresh window positionally, drops local-* and rows the window replaces', async () => {
    const { mergeWindow } = await import('../views/ChatPanel')
    const prev = [m('p1', '2026-09-01T00:00:00Z'), m('p2', '2026-09-02T00:00:00Z'), m('w1', '2026-09-03T00:00:00Z'), m('local-x', '2026-09-04T00:00:00Z')]
    const fresh = [m('w1', '2026-09-03T00:00:00Z'), m('n1', '2026-09-04T00:00:01Z')]
    expect(mergeWindow(prev, fresh).map((x) => x.id)).toEqual(['p1', 'p2', 'w1', 'n1'])
  })

  it('a previously loaded row whose created_at ties with the window\'s oldest row (same ms, earlier id) survives', async () => {
    const { mergeWindow } = await import('../views/ChatPanel')
    const tieRow = m('p0', '2026-09-03T00:00:00Z')
    const prev = [tieRow, m('p1', '2026-09-01T00:00:00Z'), m('w1', '2026-09-03T00:00:00Z'), m('local-x', '2026-09-04T00:00:00Z')]
    const fresh = [m('w1', '2026-09-03T00:00:00Z'), m('n1', '2026-09-04T00:00:01Z')]
    expect(mergeWindow(prev, fresh).map((x) => x.id)).toEqual(['p0', 'p1', 'w1', 'n1'])
    // Same tie when the window's oldest row is NOT on screen yet (timestamp fallback).
    const prev2 = [m('p1', '2026-09-01T00:00:00Z'), tieRow]
    expect(mergeWindow(prev2, fresh).map((x) => x.id)).toEqual(['p1', 'p0', 'w1', 'n1'])
  })

  it('an empty fresh window replaces nothing but the view; missing timestamps fall back to the window', async () => {
    const { mergeWindow } = await import('../views/ChatPanel')
    expect(mergeWindow([m('p1', '2026-09-01T00:00:00Z')], [])).toEqual([])
    const fresh = [{ id: 'n1', role: 'user' as const, content: 'n1', created_at: '' }]
    expect(mergeWindow([m('p1', '2026-09-01T00:00:00Z')], fresh)).toEqual(fresh)
  })
})
