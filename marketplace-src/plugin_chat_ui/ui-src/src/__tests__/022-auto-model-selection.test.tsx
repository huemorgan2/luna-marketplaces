// @vitest-environment jsdom
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ComposerModelSelect } from '../views/ChatPanel'
import { api, type ModelChain } from '@luna/lib/api'

beforeAll(() => {
  globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })
let selection: 'auto' | 'manual' | undefined
const entry = { provider: 'openai', model: 'small', fqn: 'openai:small', label: 'Small', context_window: 128000, recommended_default: false, deprecated: false }
beforeEach(() => {
  selection = 'auto'
  vi.spyOn(api, 'models').mockImplementation(async () => [{ purpose: 'reasoning', chain: [entry], selection } as ModelChain])
  vi.spyOn(api, 'modelCatalog').mockResolvedValue({ catalog: { reasoning: [entry], summarization: [], embedding: [] }, configured_providers: ['openai'], window_caps: {} })
  vi.spyOn(api, 'setModelChain').mockImplementation(async (_purpose, _chain, _policy, _caps, mode) => {
    selection = mode
    return { updated: true }
  })
})

it('persists manual and AUTO and restores the selection on remount', async () => {
  const mounted = render(<ComposerModelSelect />)
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('AUTO'))
  fireEvent.click(screen.getByTestId('composer-model-select'))
  fireEvent.click(within(screen.getByRole('listbox')).getByText('Small'))
  await waitFor(() => expect(api.setModelChain).toHaveBeenCalledWith('reasoning', ['openai:small'], undefined, undefined, 'manual'))
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('Small'))
  mounted.unmount()
  render(<ComposerModelSelect />)
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('Small'))
  fireEvent.click(screen.getByTestId('composer-model-select'))
  fireEvent.click(screen.getByTestId('model-auto-option'))
  await waitFor(() => expect(api.setModelChain).toHaveBeenLastCalledWith('reasoning', [], undefined, undefined, 'auto'))
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('AUTO'))
})

it('rolls back a rejected change', async () => {
  vi.mocked(api.setModelChain).mockResolvedValue({ updated: false, error: 'rejected' })
  render(<ComposerModelSelect />)
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('AUTO'))
  fireEvent.click(screen.getByTestId('composer-model-select'))
  fireEvent.click(within(screen.getByRole('listbox')).getByText('Small'))
  await waitFor(() => expect(api.setModelChain).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('AUTO'))
})

it('does not offer AUTO on an older core', async () => {
  selection = undefined
  render(<ComposerModelSelect />)
  await waitFor(() => expect(screen.getByTestId('composer-model-select').textContent).toContain('Small'))
  fireEvent.click(screen.getByTestId('composer-model-select'))
  expect(screen.queryByTestId('model-auto-option')).toBeNull()
})

it('keeps legacy manual writes compatible without advertising AUTO optimistically', async () => {
  selection = undefined
  vi.mocked(api.models).mockResolvedValue([{ purpose: 'reasoning', chain: [{ provider: 'openai', model: 'previous' }] }])
  render(<ComposerModelSelect />)
  await waitFor(() => expect(screen.getByTestId('composer-model-select')).toBeTruthy())
  fireEvent.click(screen.getByTestId('composer-model-select'))
  fireEvent.click(within(screen.getByRole('listbox')).getByText('Small'))
  await waitFor(() => expect(api.setModelChain).toHaveBeenCalledWith('reasoning', ['openai:small', 'openai:previous'], undefined, undefined, undefined))
  await waitFor(() => expect(screen.getByTestId('composer-model-select').hasAttribute('disabled')).toBe(false))
  fireEvent.click(screen.getByTestId('composer-model-select'))
  expect(screen.queryByTestId('model-auto-option')).toBeNull()
})
