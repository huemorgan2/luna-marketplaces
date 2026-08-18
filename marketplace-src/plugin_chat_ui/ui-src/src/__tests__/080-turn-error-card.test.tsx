// @vitest-environment jsdom
// 080/phase2 — honest turn-error card. A kind==="turn_error" row (live SSE or
// a persisted row reloaded from extra) renders a card with plain wording, the
// code as a small label, and Retry only when retryable. Retry calls back with
// the card id; the raw exception never appears.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { renderTimeline } from '../views/ChatPanel'

vi.mock('react-markdown', async () => {
  const React = await import('react')
  function MockMarkdown(props: { children?: unknown }) {
    return React.createElement('div', { 'data-testid': 'md' }, String(props.children ?? ''))
  }
  return { default: MockMarkdown, defaultUrlTransform: (u: string) => u }
})

afterEach(cleanup)

type Msg = Parameters<typeof renderTimeline>[0][number]
const STORAGE_MSG = "I couldn't reach my storage just now (the database refused new connections). Nothing was lost — please try again in a moment."

function rows(over: Partial<Msg>): Msg[] {
  return [
    { id: 'u1', role: 'user', content: 'reply with the single word pong', created_at: '2026-08-18T10:00:00Z' },
    {
      id: 'a1', role: 'assistant', content: STORAGE_MSG, kind: 'turn_error',
      error_code: 'storage_unavailable', retryable: true, created_at: '2026-08-18T10:00:01Z', ...over,
    },
  ]
}

function mount(msgs: Msg[], onRetry = vi.fn(), disabled = false) {
  const r = render(<>{renderTimeline(msgs, [], 'c1', '🌙', null, 'Luna', null, undefined, undefined,
    undefined, undefined, undefined, undefined, undefined, undefined, undefined, undefined, onRetry, disabled)}</>)
  return { ...r, onRetry }
}

describe('080 turn-error card', () => {
  it('renders the plain message, the code label and Retry for a retryable error', () => {
    const { container, onRetry } = mount(rows({}))
    const card = container.querySelector('[data-testid="turn-error-card"]') as HTMLElement
    expect(card).toBeTruthy()
    expect(card.getAttribute('data-error-code')).toBe('storage_unavailable')
    expect(card.textContent).toContain('Storage unavailable')
    expect(card.textContent).toContain(STORAGE_MSG)
    expect(card.textContent).not.toContain('TooManyConnectionsError')
    const retry = container.querySelector('[data-testid="turn-error-retry"]') as HTMLButtonElement
    expect(retry).toBeTruthy()
    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledWith('a1')
  })

  it('hides Retry when the error is not retryable (model 4xx)', () => {
    const { container } = mount(rows({ error_code: 'model_error', retryable: false, content: 'The language model returned an error: status_code: 400' }))
    expect(container.querySelector('[data-testid="turn-error-card"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="turn-error-retry"]')).toBeNull()
    expect(container.textContent).toContain('Model error')
  })

  it('disables Retry while a turn is streaming', () => {
    const { container } = mount(rows({}), vi.fn(), true)
    const retry = container.querySelector('[data-testid="turn-error-retry"]') as HTMLButtonElement
    expect(retry.disabled).toBe(true)
  })

  it('a reloaded row with kind=turn_error but no code still renders a card (internal)', () => {
    const { container } = mount(rows({ error_code: undefined, retryable: undefined }))
    const card = container.querySelector('[data-testid="turn-error-card"]') as HTMLElement
    expect(card.getAttribute('data-error-code')).toBe('internal')
    expect(container.querySelector('[data-testid="turn-error-retry"]')).toBeTruthy()
  })
})
