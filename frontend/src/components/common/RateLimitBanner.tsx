import { useEffect, useState } from 'react'
import type { RateLimitStatus } from '../../types'

interface Props {
  service: string
  status: RateLimitStatus
  onResume?: () => void
  resuming?: boolean
}

function formatCountdown(resets_at: string | null): string {
  if (!resets_at) return ''
  const diff = Math.max(0, new Date(resets_at).getTime() - Date.now())
  const mins = Math.floor(diff / 60000)
  const secs = Math.floor((diff % 60000) / 1000)
  return `${mins}m ${secs}s`
}

export function RateLimitBanner({ service, status, onResume, resuming }: Props) {
  const [countdown, setCountdown] = useState(() => formatCountdown(status.resets_at))

  useEffect(() => {
    const t = setInterval(() => setCountdown(formatCountdown(status.resets_at)), 1000)
    return () => clearInterval(t)
  }, [status.resets_at])

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 bg-gh-warning/10 border-b border-gh-warning/30 text-sm">
      <span className="text-gh-amber font-medium">{service.toUpperCase()} rate limit reached</span>
      <span className="text-gh-muted">
        ({Math.round(status.usage_percent * 100)}% used)
        {countdown && ` — resets in ${countdown}`}
      </span>
      {onResume && (
        <button
          onClick={onResume}
          disabled={resuming}
          className="ml-auto px-3 py-1 rounded bg-gh-amber/20 text-gh-amber hover:bg-gh-amber/30 transition-colors disabled:opacity-50 text-xs font-medium"
        >
          {resuming ? 'Resuming…' : 'Resume Now'}
        </button>
      )}
    </div>
  )
}
