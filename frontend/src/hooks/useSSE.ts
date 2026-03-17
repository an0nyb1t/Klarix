import { useCallback, useEffect, useRef, useState } from 'react'
import { sseUrl } from '../api/client'

interface SSEState<T> {
  lastEvent: { type: string; data: T } | null
  isConnected: boolean
  error: string | null
}

export function useSSE<T = unknown>(path: string | null): SSEState<T> & { stop: () => void } {
  const [lastEvent, setLastEvent] = useState<{ type: string; data: T } | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const esRef = useRef<EventSource | null>(null)
  const stoppedRef = useRef(false)

  const stop = useCallback(() => {
    stoppedRef.current = true
    esRef.current?.close()
    esRef.current = null
    setIsConnected(false)
  }, [])

  useEffect(() => {
    if (!path) return

    stoppedRef.current = false

    const es = new EventSource(sseUrl(path))
    esRef.current = es

    es.onopen = () => setIsConnected(true)

    const handleEvent = (type: string) => (e: MessageEvent) => {
      if (stoppedRef.current) return
      setLastEvent({ type, data: JSON.parse(e.data as string) as T })

      if (type === 'complete' || type === 'error') {
        stop()
      }
    }

    es.addEventListener('progress', handleEvent('progress'))
    es.addEventListener('paused', handleEvent('paused'))
    es.addEventListener('complete', handleEvent('complete'))
    es.addEventListener('error', handleEvent('error'))

    es.onerror = () => {
      if (!stoppedRef.current) {
        setError('SSE connection lost.')
        setIsConnected(false)
      }
    }

    return () => {
      stoppedRef.current = true
      es.close()
    }
  }, [path, stop])

  return { lastEvent, isConnected, error, stop }
}
