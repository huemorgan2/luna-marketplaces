// @vitest-environment jsdom
// 010 (006) — composer widget zone + Curiosity setup stepper.
// The stepper self-fetches the curiosity journey, renders the six-dot rail
// while onboarding is in progress, and returns null (collapsing the zone)
// when there is no mission, the journey reached work phase, or the fetch
// fails. A ui.plugin.event from plugin-curiosity re-fetches.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor, act } from '@testing-library/react'
import { CuriositySetupStepper } from '../views/composer/CuriositySetupStepper'
import { composerWidgets } from '../lib/composerWidgets'

const h = vi.hoisted(() => ({
  handlers: null as null | { onPluginEvent?: (info: { plugin: string; event: string }) => void },
}))

vi.mock('@luna/lib/api', () => ({
  getToken: () => 'tok',
  subscribeApprovalEvents: vi.fn((handlers) => {
    h.handlers = handlers
    return () => { h.handlers = null }
  }),
}))

const STEPS = [
  { label: 'Hear', state: 'done', when: 'Aug 1' },
  { label: 'Reflect', state: 'done', when: 'Aug 1' },
  { label: 'Prove', state: 'now', when: 'now' },
  { label: 'Agree', state: 'todo', when: '' },
  { label: 'Earn', state: 'todo', when: '' },
  { label: 'Own', state: 'todo', when: 'your call' },
]

function mockOverview(body: unknown, ok = true) {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok, json: async () => body })))
}

beforeEach(() => {
  cleanup()
  h.handlers = null
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('010 curiosity setup stepper', () => {
  it('is registered as a composer widget', () => {
    expect(composerWidgets.some((w) => w.id === 'curiosity-setup-stepper')).toBe(true)
  })

  it('renders the six-step rail while onboarding is in progress', async () => {
    mockOverview({ journey: { steps: STEPS }, mission: { agent_phase: 'setup' } })
    render(<CuriositySetupStepper />)
    expect(await screen.findByTestId('curiosity-stepper')).toBeTruthy()
    for (const s of STEPS) expect(screen.getByText(s.label)).toBeTruthy()
  })

  it('renders nothing once the mission reaches work phase', async () => {
    mockOverview({ journey: { steps: STEPS }, mission: { agent_phase: 'work' } })
    render(<CuriositySetupStepper />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('curiosity-stepper')).toBeNull()
  })

  it('renders nothing without a mission', async () => {
    mockOverview({ journey: null, mission: null })
    render(<CuriositySetupStepper />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('curiosity-stepper')).toBeNull()
  })

  it('renders nothing when the plugin/endpoint is absent', async () => {
    mockOverview({}, false)
    render(<CuriositySetupStepper />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByTestId('curiosity-stepper')).toBeNull()
  })

  it('re-fetches on a plugin-curiosity ui.plugin.event', async () => {
    mockOverview({ journey: { steps: STEPS }, mission: { agent_phase: 'setup' } })
    render(<CuriositySetupStepper />)
    await screen.findByTestId('curiosity-stepper')
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.length
    await act(async () => {
      h.handlers?.onPluginEvent?.({ plugin: 'plugin-curiosity', event: 'mission.updated' })
    })
    await waitFor(() =>
      expect((fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(calls),
    )
  })
})
