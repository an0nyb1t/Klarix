import { useEffect, useState } from 'react'

interface Props {
  resetsAt: string | null
  onOpenSettings: () => void
  onDismiss: () => void
}

function formatCountdown(resetsAt: string | null): string {
  if (!resetsAt) return ''
  const diff = Math.max(0, new Date(resetsAt).getTime() - Date.now())
  const totalSeconds = Math.floor(diff / 1000)
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = totalSeconds % 60
  if (h > 0) return `${h}h ${m}m ${s}s`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function formatResetTime(resetsAt: string | null): string {
  if (!resetsAt) return 'soon'
  return new Date(resetsAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
}

export function ClaudeCodeLimitPopup({ resetsAt, onOpenSettings, onDismiss }: Props) {
  const [countdown, setCountdown] = useState(() => formatCountdown(resetsAt))

  useEffect(() => {
    setCountdown(formatCountdown(resetsAt))
    const interval = setInterval(() => {
      setCountdown(formatCountdown(resetsAt))
    }, 1000)
    return () => clearInterval(interval)
  }, [resetsAt])

  return (
    <div className="fixed bottom-4 right-4 z-50 w-80 rounded-xl border border-gh-warning/30 bg-gh-surface shadow-2xl">
      <div className="px-4 py-3 border-b border-gh-border flex items-center justify-between">
        <span className="text-gh-warning text-sm font-medium">Claude Code limit reached</span>
        <button
          onClick={onDismiss}
          className="text-gh-muted hover:text-gh-text transition-colors text-xs"
          aria-label="Dismiss"
        >
          x
        </button>
      </div>
      <div className="px-4 py-3 space-y-2">
        <p className="text-gh-text text-sm">
          Your Claude Code subscription limit has been reached.
          {resetsAt && (
            <> It resets at {formatResetTime(resetsAt)}.</>
          )}
        </p>
        {countdown && (
          <p className="text-gh-muted text-xs">
            Resets in: <span className="text-gh-text font-mono">{countdown}</span>
          </p>
        )}
      </div>
      <div className="px-4 py-3 border-t border-gh-border flex gap-2">
        <button
          onClick={onOpenSettings}
          className="flex-1 px-3 py-1.5 rounded-lg bg-gh-accent text-gh-bg text-xs font-medium hover:bg-gh-accent/90 transition-colors"
        >
          Switch to API provider
        </button>
        <button
          onClick={onDismiss}
          className="px-3 py-1.5 rounded-lg bg-gh-surface border border-gh-border text-gh-muted text-xs hover:text-gh-text transition-colors"
        >
          Dismiss
        </button>
      </div>
    </div>
  )
}
