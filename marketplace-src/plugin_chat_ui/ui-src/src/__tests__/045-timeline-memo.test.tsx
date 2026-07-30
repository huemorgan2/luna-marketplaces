// @vitest-environment jsdom
// 045/phase05 — streaming re-render diet. MemoBubble must skip unchanged
// messages when only the last one grows (rAF-batched deltas), and the
// timeline useMemo must not re-parse markdown on unrelated state changes
// (composer keystrokes).
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react'
import * as RM from 'react-markdown'
import { ChatPanel, renderTimeline } from '../views/ChatPanel'

const h = vi.hoisted(() => ({
  conversations: vi.fn(),
  messages: vi.fn(),
}))

// Counting stand-in for react-markdown: every render pushes its source text.
vi.mock('react-markdown', async () => {
  const React = await import('react')
  const renders: string[] = []
  function MockMarkdown(props: { children?: unknown }) {
    renders.push(String(props.children ?? ''))
    return React.createElement('div', { 'data-testid': 'md' }, String(props.children ?? ''))
  }
  return { default: MockMarkdown, defaultUrlTransform: (u: string) => u, __renders: renders }
})

vi.mock('@luna/lib/api', () => ({
  api: {
    planTasks: vi.fn(async () => ({ tasks: [], created_at: null, turn_active: false })),
    conversations: h.conversations,
    messages: h.messages,
    context: vi.fn(async () => null),
    identity: vi.fn(async () => ({ name: 'Luna', emoji: '🌙' })),
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

const renders = (RM as unknown as { __renders: string[] }).__renders

function msgAt(i: number, content: string) {
  return {
    id: `m${i}`,
    role: 'assistant' as const,
    content,
    created_at: new Date(1753000000000 + i * 1000).toISOString(),
  }
}

const NO_APPROVALS: never[] = []

function Harness({ messages }: { messages: ReturnType<typeof msgAt>[] }) {
  return <>{renderTimeline(messages, NO_APPROVALS, 'c1', '🌙', null, 'Luna')}</>
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  renders.length = 0
})

describe('045 timeline memo', () => {
  it('growing only the last of 50 messages re-parses only that bubble', () => {
    const messages = Array.from({ length: 50 }, (_, i) => msgAt(i, `msg ${i}`))
    const { rerender } = render(<Harness messages={messages} />)
    expect(renders.filter((r) => r === 'msg 0').length).toBeGreaterThan(0)
    const mark = renders.length

    // Same object references except the last one — exactly what the rAF
    // flush's setMessages produces.
    const grown = messages.slice(0, 49).concat({ ...messages[49], content: 'msg 49 plus-delta' })
    rerender(<Harness messages={grown} />)

    const fresh = renders.slice(mark)
    expect(fresh).toContain('msg 49 plus-delta')
    expect(fresh).not.toContain('msg 0')
    expect(fresh).not.toContain('msg 25')
    // One bubble's worth of markdown work, not 50.
    expect(fresh.length).toBeLessThanOrEqual(2)
  })

  it('composer keystrokes do not re-render any bubble (timeline useMemo)', async () => {
    h.conversations.mockResolvedValue([
      { id: 'c1', title: 'chat', created_at: '2026-07-21T00:00:00Z', updated_at: '2026-07-21T00:00:00Z' },
    ])
    h.messages.mockResolvedValue([msgAt(0, 'hello there'), msgAt(1, 'second message')])
    render(<ChatPanel identity={null} />)
    await waitFor(() => expect(renders).toContain('hello there'))
    await waitFor(() => expect(screen.getByPlaceholderText(/Message /)).toBeTruthy())
    const mark = renders.length

    const box = screen.getByPlaceholderText(/Message /) as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'typing…' } })
    fireEvent.change(box, { target: { value: 'typing more…' } })

    expect(renders.length).toBe(mark)
  })
})
