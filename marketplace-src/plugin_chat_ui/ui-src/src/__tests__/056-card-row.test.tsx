// @vitest-environment jsdom
// 056 — standalone plugin cards: kind==="card" renders bubble-less and
// full-width; the luna:embed:height contract resizes only from the card's own
// iframe; empty assistant replies (no embed) render nothing; legacy rows are
// untouched.
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest'
import { render, waitFor, cleanup } from '@testing-library/react'
import { renderTimeline } from '../views/ChatPanel'

vi.mock('react-markdown', async () => {
  const React = await import('react')
  function MockMarkdown(props: { children?: unknown }) {
    return React.createElement('div', { 'data-testid': 'md' }, String(props.children ?? ''))
  }
  return { default: MockMarkdown, defaultUrlTransform: (u: string) => u }
})

// Pass embed HTML through untouched — no asset fetching in jsdom.
vi.mock('@luna/lib/embedAssets', () => ({
  inlineEmbedAssets: async (html: string) => html,
  forceEagerEmbedImages: (html: string) => html,
}))

const HTML = '<html><body>scene</body></html>'

type Msg = Parameters<typeof renderTimeline>[0][number]

function msg(over: Partial<Msg> & { id: string }): Msg {
  return {
    role: 'assistant',
    content: '',
    created_at: new Date(1753000000000).toISOString(),
    ...over,
  } as Msg
}

function draw(messages: Msg[]) {
  return render(<>{renderTimeline(messages, [], 'c1', '🌙', null, 'Luna')}</>)
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
})

describe('056 standalone cards', () => {
  it('kind==="card" renders full-width, no avatar, no bubble chrome', async () => {
    const { container } = draw([
      msg({ id: 'c1', kind: 'card', embed_iframe: HTML }),
    ])
    const row = container.querySelector('[data-testid="card-row"]')
    expect(row).toBeTruthy()
    await waitFor(() =>
      expect(container.querySelector('[data-testid="card-embed"]')).toBeTruthy(),
    )
    // No bubble wrapper, no avatar in the card row.
    expect(container.querySelector('.max-w-\\[80\\%\\]')).toBeNull()
    expect(row!.querySelector('img')).toBeNull()
    // The embed column stretches (flex-1), it is not the 80% bubble.
    expect(row!.innerHTML).toContain('flex-1')
  })

  it('legacy assistant message renders the bubble exactly as before', () => {
    const { container } = draw([msg({ id: 'a1', content: 'hello there' })])
    expect(container.querySelector('.max-w-\\[80\\%\\]')).toBeTruthy()
    expect(container.querySelector('[data-testid="card-row"]')).toBeNull()
  })

  it('empty assistant reply with no embed renders nothing', () => {
    const { container } = draw([msg({ id: 'e1', content: '   ' })])
    expect(container.querySelector('.max-w-\\[80\\%\\]')).toBeNull()
    expect(container.querySelector('[data-testid="md"]')).toBeNull()
  })

  it('empty assistant reply WITH a legacy embed still renders its bubble', async () => {
    const { container } = draw([
      msg({ id: 'e2', content: '', embed_iframe: HTML }),
    ])
    expect(container.querySelector('.max-w-\\[80\\%\\]')).toBeTruthy()
    await waitFor(() =>
      expect(container.querySelector('[data-testid="bubble-embed"]')).toBeTruthy(),
    )
  })

  it('luna:embed:height resizes only from the card iframe, capped at 1400', async () => {
    const { container } = draw([
      msg({ id: 'c2', kind: 'card', embed_iframe: HTML }),
    ])
    await waitFor(() =>
      expect(container.querySelector('[data-testid="card-embed"]')).toBeTruthy(),
    )
    const frame = container.querySelector('[data-testid="card-embed"]') as HTMLIFrameElement

    // A foreign source must NOT resize the card.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'luna:embed:height', height: 640 },
        source: window,
      }),
    )
    await new Promise((r) => setTimeout(r, 0))
    expect(frame.style.height).toBe('')

    // The card's own contentWindow does.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'luna:embed:height', height: 640 },
        source: frame.contentWindow,
      }),
    )
    await waitFor(() => expect(frame.style.height).toBe('640px'))

    // And the cap holds against hostile/buggy embeds.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'luna:embed:height', height: 5000 },
        source: frame.contentWindow,
      }),
    )
    await waitFor(() => expect(frame.style.height).toBe('1400px'))
  })
})
