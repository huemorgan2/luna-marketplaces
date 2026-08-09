/**
 * Composer widget registry (plan 010, from luna-plugins plan 006) — the zone
 * directly above the message composer. Each entry is a native React component
 * that self-fetches, self-subscribes, and self-hides (returns null when it
 * has nothing to show), so the zone collapses to nothing on its own. Adding
 * a widget above the message area = adding an entry here.
 */

import type { ReactNode } from 'react'
import { CuriositySetupStepper } from '../views/composer/CuriositySetupStepper'

export const composerWidgets: { id: string; Component: () => ReactNode }[] = [
  { id: 'curiosity-setup-stepper', Component: CuriositySetupStepper },
]
