import { useEffect, useState } from 'react'
import { settingsApi } from '../api/settings'
import type { ClaudeCodeStatus } from '../types'

const POLL_INTERVAL = 60_000 // 60s — per SPEC

export function useClaudeCodeStatus(enabled: boolean): ClaudeCodeStatus | null {
  const [status, setStatus] = useState<ClaudeCodeStatus | null>(null)

  useEffect(() => {
    if (!enabled) {
      setStatus(null)
      return
    }

    let mounted = true

    const fetch = async () => {
      try {
        const data = await settingsApi.claudeCodeStatus()
        if (mounted) setStatus(data)
      } catch {
        // Silent — status is informational
      }
    }

    fetch()
    const interval = setInterval(fetch, POLL_INTERVAL)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [enabled])

  return status
}
