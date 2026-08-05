// @vitest-environment jsdom
// 071 — reasoning trace UI. Live shows a ticking "Reasoning Ns" panel; folded
// shows a clickable "Thought Ns" pill that toggles the full trace open.
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { ReasoningBlock } from '../views/ChatPanel'

afterEach(cleanup)

describe('ReasoningBlock', () => {
  it('renders the live trace expanded with a Reasoning label', () => {
    render(<ReasoningBlock text="weighing the options" ms={2400} live />)
    expect(screen.getByTestId('reasoning-pill').textContent).toContain('Reasoning')
    // seconds = round(2400/1000) = 2
    expect(screen.getByTestId('reasoning-pill').textContent).toContain('2s')
    // live trace is always visible
    expect(screen.getByTestId('reasoning-text').textContent).toBe('weighing the options')
  })

  it('folds to a Thought Ns pill that toggles the trace', () => {
    render(<ReasoningBlock text="the full reasoning" ms={5600} live={false} />)
    const pill = screen.getByTestId('reasoning-pill')
    expect(pill.textContent).toContain('Thought 6s')
    // collapsed by default
    expect(screen.queryByTestId('reasoning-text')).toBeNull()
    // click expands
    fireEvent.click(pill)
    expect(screen.getByTestId('reasoning-text').textContent).toBe('the full reasoning')
    // click again collapses
    fireEvent.click(pill)
    expect(screen.queryByTestId('reasoning-text')).toBeNull()
  })

  it('never labels less than 1s', () => {
    render(<ReasoningBlock text="quick" ms={200} live={false} />)
    expect(screen.getByTestId('reasoning-pill').textContent).toContain('Thought 1s')
  })
})
