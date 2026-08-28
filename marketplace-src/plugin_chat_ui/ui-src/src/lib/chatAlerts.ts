// 011 — agent-behaviour feedback banner: regex-only frustration detection on
// the user's outgoing messages (no LLM, by design) plus per-conversation
// suppression so a dismissed/answered banner stays quiet for a while.

/**
 * Patterns are deliberately conservative: a false banner is worse than a
 * missed one. Tuned so "did you see the game?" or "well done!" never fire,
 * while "well - did u do it?", "you're not hearing me", profanity and
 * triple-bangs do.
 */
export const FRUSTRATION_PATTERNS: RegExp[] = [
  /\b(fuck\w*|wtf|bullshit|goddamn|dammit|damn it)\b/i,
  /!{3,}/,
  /\b(you['’]?re?|u['’]?re?|ur) not (hearing|listening|understanding|getting|reading)\b/i,
  /\bnot what i (asked|said|meant|wanted)\b/i,
  // "did you/u ..." only counts with an impatience marker: a leading "well",
  // an intensifier, or a bare "do it / fix it"-style object + question mark.
  /^\s*well\b.{0,60}\?/i,
  /\bdid (you|u) (actually|even|really)\b.{0,60}\?/i,
  /\bdid (you|u) (do|fix|change|update|run|read) (it|that|this|them|anything)\b.{0,40}\?/i,
  /\b(again|still)\s+(broken|wrong|failing|not working|the same)\b/i,
  /\b(still|again) (doesn['’]?t|does not|didn['’]?t|won['’]?t) work\b/i,
  /\bstop (doing|saying|repeating|ignoring)\b/i,
  /\bare you (even|serious|kidding)\b/i,
  /\bhow many times\b/i,
  /\bi (already|just) (told|said|asked)\b/i,
]

export function matchesFrustration(text: string): boolean {
  if (!text) return false
  return FRUSTRATION_PATTERNS.some((re) => re.test(text))
}

// Dismissing and answering share one suppression key: either way the banner
// stays away for the cooldown window, per conversation.
const KEY_PREFIX = 'chatui.alert.agent-feedback.'
const DEFAULT_COOLDOWN_HOURS = 24

export function alertSuppressed(convId: string, now: number = Date.now()): boolean {
  try {
    const raw = localStorage.getItem(KEY_PREFIX + convId)
    if (!raw) return false
    const until = Number(raw)
    return Number.isFinite(until) && now < until
  } catch {
    return false
  }
}

export function suppressAlert(
  convId: string,
  hours: number = DEFAULT_COOLDOWN_HOURS,
  now: number = Date.now(),
): void {
  try {
    localStorage.setItem(KEY_PREFIX + convId, String(now + hours * 3_600_000))
  } catch {
    /* storage unavailable — banner may just show again */
  }
}
