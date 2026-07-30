// @vitest-environment jsdom
// 057 — card-action bridge: a card iframe posts luna:card:action; the shell
// performs the authed HTTP call and replies luna:card:result to that iframe
// only. Confused-deputy guards: only the posting plugin's route prefix is
// callable, and cards without a source get no bridge at all.
import { describe, it, expect, vi, beforeEach, beforeAll, afterEach } from 'vitest'
import { render, waitFor, cleanup } from '@testing-library/react'
import { renderTimeline } from '../views/ChatPanel'
import { tokenStorageKey } from '@luna/lib/api'

vi.mock('react-markdown', async () => {
  const React = await import('react')
  function MockMarkdown(props: { children?: unknown }) {
    return React.createElement('div', { 'data-testid': 'md' }, String(props.children ?? ''))
  }
  return { default: MockMarkdown, defaultUrlTransform: (u: string) => u }
})

vi.mock('@luna/lib/embedAssets', () => ({
  inlineEmbedAssets: async (html: string) => html,
  forceEagerEmbedImages: (html: string) => html,
}))

const HTML = '<html><body>scene</body></html>'
const PLUGIN = 'plugin-linear-ascent'

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
  return render(<>{renderTimeline(messages, [], 'conv-1', '🌙', null, 'Luna')}</>)
}

async function drawCard(over: Partial<Msg> = {}) {
  const { container } = draw([
    msg({ id: 'm1', kind: 'card', embed_iframe: HTML, source: PLUGIN, ...over }),
  ])
  await waitFor(() =>
    expect(container.querySelector('[data-testid="card-embed"]')).toBeTruthy(),
  )
  return container.querySelector('[data-testid="card-embed"]') as HTMLIFrameElement
}

function postAction(frame: HTMLIFrameElement, data: unknown) {
  window.dispatchEvent(
    new MessageEvent('message', { data, source: frame.contentWindow }),
  )
}

const fetchMock = vi.fn()

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  cleanup()
  localStorage.clear()
  localStorage.setItem(tokenStorageKey(), 'tkn-1')
  fetchMock.mockReset()
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ ok: true, message_id: 'msg-2' }),
  })
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('057 card-action bridge', () => {
  it('performs the authed call and replies to the card iframe', async () => {
    const frame = await drawCard()
    const reply = vi.spyOn(frame.contentWindow!, 'postMessage')

    postAction(frame, {
      type: 'luna:card:action',
      nonce: 'n1',
      path: `/api/p/${PLUGIN}/act`,
      body: { option: 'attack', scene_id: 's1' },
    })

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe(`/api/p/${PLUGIN}/act`)
    expect(init.method).toBe('POST')
    expect(init.headers.Authorization).toBe('Bearer tkn-1')
    // The shell injects conversation/message ids — the card can't spoof them.
    expect(JSON.parse(init.body)).toEqual({
      option: 'attack',
      scene_id: 's1',
      conversation_id: 'conv-1',
      message_id: 'm1',
    })

    await waitFor(() =>
      expect(reply).toHaveBeenCalledWith(
        {
          type: 'luna:card:result',
          nonce: 'n1',
          ok: true,
          status: 200,
          body: { ok: true, message_id: 'msg-2' },
        },
        '*',
      ),
    )
  })

  it('refuses paths outside the posting plugin route prefix', async () => {
    const frame = await drawCard()
    const reply = vi.spyOn(frame.contentWindow!, 'postMessage')

    postAction(frame, {
      type: 'luna:card:action',
      nonce: 'n2',
      path: '/api/p/plugin-vault/secrets',
      body: {},
    })

    await waitFor(() =>
      expect(reply).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'luna:card:result', nonce: 'n2', ok: false }),
        '*',
      ),
    )
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ignores actions from a foreign window', async () => {
    await drawCard()
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'luna:card:action', nonce: 'n3', path: `/api/p/${PLUGIN}/act`, body: {} },
        source: window,
      }),
    )
    await new Promise((r) => setTimeout(r, 0))
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('cards without a source get no bridge', async () => {
    const frame = await drawCard({ source: undefined })
    postAction(frame, {
      type: 'luna:card:action',
      nonce: 'n4',
      path: `/api/p/${PLUGIN}/act`,
      body: {},
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
