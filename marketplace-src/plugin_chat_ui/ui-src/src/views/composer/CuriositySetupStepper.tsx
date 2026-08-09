/**
 * Curiosity setup stepper — the six-dot adoption journey rail
 * (Hear → Reflect → Prove → Agree → Earn → Own), rendered transparently
 * above the composer while curiosity onboarding is in progress.
 *
 * Self-contained composer widget (plan 010, from luna-plugins plan 006):
 * fetches `/missions/overview` `.journey.steps`, re-fetches on any
 * `ui.plugin.event` from plugin-curiosity, and renders null — collapsing
 * the zone — when there is no mission, the journey is done (work phase),
 * or the plugin/endpoint is absent.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'
import { getToken, subscribeApprovalEvents } from '@luna/lib/api'
import { cn } from '@luna/lib/cn'

type JourneyStep = { label: string; state: 'done' | 'now' | 'todo'; when: string }

async function fetchSteps(): Promise<JourneyStep[] | null> {
  const t = getToken()
  const r = await fetch('/api/p/plugin-curiosity/missions/overview', {
    headers: t ? { Authorization: `Bearer ${t}` } : undefined,
  })
  if (!r.ok) return null
  const o = (await r.json()) as {
    journey?: { steps?: JourneyStep[] } | null
    mission?: { agent_phase?: string | null } | null
  }
  const steps = o.journey?.steps
  if (!steps?.length || !o.mission) return null
  // Own reached — setup is over; the rail retires.
  if (o.mission.agent_phase === 'work') return null
  return steps
}

export function CuriositySetupStepper() {
  const [steps, setSteps] = useState<JourneyStep[] | null>(null)
  const alive = useRef(true)
  const refresh = useCallback(() => {
    fetchSteps()
      .then((s) => { if (alive.current) setSteps(s) })
      .catch(() => { if (alive.current) setSteps(null) })
  }, [])
  useEffect(() => {
    alive.current = true
    refresh()
    const unsubscribe = subscribeApprovalEvents({
      onPluginEvent: (info) => {
        if (info.plugin === 'plugin-curiosity') refresh()
      },
    })
    return () => {
      alive.current = false
      unsubscribe()
    }
  }, [refresh])
  if (!steps) return null
  return (
    <div data-testid="curiosity-stepper" className="flex items-start pb-2">
      {steps.map((s, i) => (
        <div key={s.label} className="relative flex-1 text-center">
          {/* rail segment connecting to the previous dot */}
          {i > 0 && (
            <div
              className={cn(
                'absolute top-[11px] -left-1/2 w-full h-0.5',
                s.state === 'todo' ? 'bg-white/10' : 'bg-emerald-400/40',
              )}
            />
          )}
          <div
            className={cn(
              'relative z-[1] mx-auto flex h-[22px] w-[22px] items-center justify-center rounded-full border text-[12px]',
              s.state === 'done' && 'border-emerald-400/60 bg-emerald-400/10 text-emerald-400',
              s.state === 'now' &&
                'border-luna-500 bg-luna-500 text-white shadow-[0_0_0_5px_rgba(139,92,246,0.18)]',
              s.state === 'todo' && 'border-white/10 bg-ink-900/60 text-ink-500',
            )}
          >
            {s.state === 'done' && <Check className="h-3 w-3" />}
          </div>
          <div
            className={cn(
              'mt-2 text-xs font-semibold',
              s.state === 'now' ? 'text-ink-100' : 'text-ink-400',
            )}
          >
            {s.label}
          </div>
          {s.when && <div className="mt-0.5 text-[11px] text-ink-500">{s.when}</div>}
        </div>
      ))}
    </div>
  )
}
