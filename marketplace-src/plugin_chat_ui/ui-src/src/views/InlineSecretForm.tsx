/**
 * 006.708 — in-chat secret form. The value POSTs straight to the vault
 * plugin route; it never enters a message, SSE frame, or the model context.
 */

import { useState, type CSSProperties } from 'react'
import { KeyRound, CheckCircle2, XCircle, Loader2, ShieldCheck } from 'lucide-react'
import { cn } from '@luna/lib/cn'
import { api, type SecretRequestSummary } from '@luna/lib/api'

export function InlineSecretForm({
  req,
  onResolved,
}: {
  req: SecretRequestSummary
  onResolved?: (status: 'fulfilled' | 'cancelled') => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ----- resolved chip -----
  if (req.status !== 'pending') {
    const ok = req.status === 'fulfilled'
    return (
      <div className="mt-2">
        <div
          data-testid="secret-form-resolved"
          data-status={req.status}
          className={cn(
            'inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-xs text-white',
            ok ? 'bg-emerald-600' : 'bg-ink-600',
          )}
        >
          {ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
          <span className="font-mono">{req.name}</span>
          <span className="font-medium">{ok ? 'stored ✓' : 'dismissed'}</span>
        </div>
      </div>
    )
  }

  async function submit() {
    if (!value.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      await api.secretRequests.fulfill(req.id, value)
      setValue('')
      onResolved?.('fulfilled')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await api.secretRequests.cancel(req.id)
      onResolved?.('cancelled')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      data-testid="secret-form"
      className="mt-2 max-w-md rounded-xl border border-amber-500/30 bg-amber-950/20 p-3.5"
    >
      <div className="flex items-center gap-2 mb-1.5">
        <KeyRound className="h-4 w-4 text-amber-400" />
        <span className="text-sm font-medium text-ink-100 font-mono">{req.name}</span>
        <span className="text-[10px] uppercase tracking-wider rounded bg-amber-500/20 border border-amber-500/30 text-amber-300 px-1.5 py-0.5">
          {req.kind}
        </span>
      </div>
      {req.reason && <div className="text-xs text-ink-400 mb-2">{req.reason}</div>}
      <div className="flex items-center gap-1.5 text-[11px] text-emerald-400/90 mb-2.5">
        <ShieldCheck className="h-3.5 w-3.5" />
        Goes directly to the encrypted vault — never enters the conversation.
      </div>
      <form
        onSubmit={(e) => { e.preventDefault(); void submit() }}
        className="flex items-center gap-2"
      >
        <input
          // type="text" + CSS masking (not type="password"): password managers
          // autofill password fields with saved credentials without firing
          // onChange, leaving the box visually full of dots while `value` is
          // still empty. Masking via text-security keeps the dots for typed
          // input only.
          type="text"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          data-lpignore="true"
          data-1p-ignore="true"
          data-bwignore="true"
          data-form-type="other"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Paste the secret value…"
          data-testid="secret-form-input"
          style={value ? { WebkitTextSecurity: 'disc' } as CSSProperties : undefined}
          className="flex-1 rounded-lg bg-ink-900/80 border border-white/10 px-3 py-1.5 text-sm text-ink-100 placeholder-ink-500 outline-none focus:border-amber-500/50"
        />
        <button
          type="submit"
          disabled={busy || !value.trim()}
          data-testid="secret-form-submit"
          className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 hover:bg-amber-500 disabled:bg-ink-800 disabled:text-ink-500 transition text-white text-xs font-medium py-1.5 px-3"
        >
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          Submit
        </button>
        <button
          type="button"
          onClick={() => void cancel()}
          disabled={busy}
          data-testid="secret-form-cancel"
          className="rounded-lg border border-white/10 hover:bg-white/5 transition text-ink-300 text-xs font-medium py-1.5 px-3"
        >
          Cancel
        </button>
      </form>
      {error && <div className="mt-2 text-xs text-rose-400">{error}</div>}
    </div>
  )
}
