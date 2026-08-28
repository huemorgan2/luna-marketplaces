// @vitest-environment jsdom
// 011 — agent-behaviour feedback banner: regex matcher hits/misses, the
// per-conversation suppression window, and the one-line banner's actions.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { matchesFrustration, alertSuppressed, suppressAlert } from '../lib/chatAlerts'
import { AgentFeedbackBanner } from '../views/ChatPanel'

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

beforeEach(() => {
  cleanup()
  localStorage.clear()
})

describe('matchesFrustration', () => {
  it.each([
    'fuck this thing',
    'WTF is going on',
    'this is bullshit',
    'why is it broken again!!!',
    "you're not hearing me",
    'youre not listening to me',
    'that is not what I asked for',
    'well - did u do it?',
    'did you even read the file?',
    'did you actually fix anything?',
    'did you fix it or not?',
    'it is still broken',
    'still doesn’t work',
    'stop repeating yourself',
    'are you even trying',
    'how many times do I have to say it',
    'I already told you the path',
  ])('fires on %j', (text) => {
    expect(matchesFrustration(text)).toBe(true)
  })

  it.each([
    '',
    'hello',
    'great, thanks!!',
    'well done',
    'did you see the game?',
    'can you fix the login bug?',
    'please update the docs',
    'the tests are green now',
    'what does this error mean?',
  ])('stays quiet on %j', (text) => {
    expect(matchesFrustration(text)).toBe(false)
  })
})

describe('suppression window', () => {
  it('suppresses per conversation and expires', () => {
    const t0 = 1_700_000_000_000
    expect(alertSuppressed('c1', t0)).toBe(false)
    suppressAlert('c1', 24, t0)
    expect(alertSuppressed('c1', t0 + 1000)).toBe(true)
    expect(alertSuppressed('c2', t0 + 1000)).toBe(false) // other conversation untouched
    expect(alertSuppressed('c1', t0 + 24 * 3_600_000 + 1)).toBe(false) // expired
  })

  it('ignores garbage stored values', () => {
    localStorage.setItem('chatui.alert.agent-feedback.c1', 'not-a-number')
    expect(alertSuppressed('c1')).toBe(false)
  })
})

describe('AgentFeedbackBanner', () => {
  it('renders one line with three verdicts and reports the click', () => {
    const onVerdict = vi.fn()
    const { getByTestId, getByText } = render(
      <AgentFeedbackBanner onVerdict={onVerdict} onDismiss={() => {}} />,
    )
    getByText('Is the agent doing a good job?')
    fireEvent.click(getByTestId('agent-feedback-bad'))
    expect(onVerdict).toHaveBeenCalledWith('bad')
    fireEvent.click(getByTestId('agent-feedback-good'))
    expect(onVerdict).toHaveBeenCalledWith('good')
  })

  it('the X dismisses', () => {
    const onDismiss = vi.fn()
    const { getByTestId } = render(
      <AgentFeedbackBanner onVerdict={() => {}} onDismiss={onDismiss} />,
    )
    fireEvent.click(getByTestId('agent-feedback-dismiss'))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
