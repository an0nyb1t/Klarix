import { useEffect, useState } from 'react'
import { settingsApi } from '../api/settings'
import type { RateLimitStatus } from '../types'

const POLL_INTERVAL = 30_000 // 30s

export function useRateLimits() {
  const [limits, setLimits] = useState<Record<string, RateLimitStatus>>({})

  useEffect(() => {
    let mounted = true

    const fetch = async () => {
      try {
        const data = await settingsApi.rateLimits()
        if (mounted) setLimits(data)
      } catch {
        // Silent — rate limits are informational
      }
    }

    fetch()
    const interval = setInterval(fetch, POLL_INTERVAL)
    return () => {
      mounted = false
      clearInterval(interval)
    }
  }, [])

  const isPaused = Object.values(limits).some(l => l.is_paused)
  const pausedServices = Object.entries(limits)
    .filter(([, v]) => v.is_paused)
    .map(([k]) => k)

  return { limits, isPaused, pausedServices }
}
