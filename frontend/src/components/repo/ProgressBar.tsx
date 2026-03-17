import { useEffect, useState } from 'react'
import { useSSE } from '../../hooks/useSSE'
import type { ProgressEvent, PausedEvent } from '../../types'
import { reposApi } from '../../api/repositories'

interface Props {
  repoId: string
  repoName: string
  onComplete: () => void
}

type SSEData = ProgressEvent | PausedEvent | { repo_id: string } | { message: string }

function formatCountdown(resets_at: string | null): string {
  if (!resets_at) return ''
  const diff = Math.max(0, new Date(resets_at).getTime() - Date.now())
  const mins = Math.floor(diff / 60000)
  const secs = Math.floor((diff % 60000) / 1000)
  return `${mins}m ${secs}s`
}

export function ProgressBar({ repoId, repoName, onComplete }: Props) {
  const { lastEvent, error: sseError } = useSSE<SSEData>(`/api/repos/${repoId}/progress`)

  const [progress, setProgress] = useState<ProgressEvent | null>(null)
  const [paused, setPaused] = useState<PausedEvent | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [resuming, setResuming] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [countdown, setCountdown] = useState('')

  useEffect(() => {
    if (!lastEvent) return

    if (lastEvent.type === 'progress') {
      setProgress(lastEvent.data as ProgressEvent)
      setPaused(null)
    } else if (lastEvent.type === 'paused') {
      setPaused(lastEvent.data as PausedEvent)
    } else if (lastEvent.type === 'complete') {
      onComplete()
    } else if (lastEvent.type === 'error') {
      setFailed((lastEvent.data as { message: string }).message)
    }
  }, [lastEvent, onComplete])

  useEffect(() => {
    if (!paused?.resets_at) return
    const t = setInterval(() => setCountdown(formatCountdown(paused.resets_at)), 1000)
    setCountdown(formatCountdown(paused.resets_at))
    return () => clearInterval(t)
  }, [paused?.resets_at])

  const handleRetry = async () => {
    setRetrying(true)
    try {
      await reposApi.sync(repoId)
      setFailed(null)
    } catch {
      // SSE will report the new error if it fails again
    } finally {
      setRetrying(false)
    }
  }

  const handleResume = async () => {
    setResuming(true)
    try {
      await reposApi.resume(repoId)
      setPaused(null)
    } catch {
      // Will reconnect via SSE
    } finally {
      setResuming(false)
    }
  }

  const percent = progress && progress.total > 0
    ? Math.round((progress.current / progress.total) * 100)
    : null

  return (
    <div className="flex-1 flex items-center justify-center px-8">
      <div className="w-full max-w-md">
        <p className="text-gh-muted text-xs mb-1">Analyzing repository</p>
        <h2 className="text-gh-text font-semibold text-lg mb-6">{repoName}</h2>

        {failed ? (
          <div className="rounded-lg border border-gh-danger/30 bg-gh-danger/10 p-4">
            <p className="text-gh-danger text-sm font-medium">Ingestion failed</p>
            <p className="text-gh-muted text-xs mt-1">{failed}</p>
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="mt-3 px-4 py-1.5 rounded bg-gh-danger/20 text-gh-danger hover:bg-gh-danger/30 transition-colors text-xs font-medium disabled:opacity-50"
            >
              {retrying ? 'Retrying…' : 'Retry'}
            </button>
          </div>
        ) : paused ? (
          <div className="rounded-lg border border-gh-amber/30 bg-gh-amber/10 p-4">
            <p className="text-gh-amber text-sm font-medium">Paused — rate limit reached</p>
            <p className="text-gh-muted text-xs mt-1">{paused.message}</p>
            {countdown && (
              <p className="text-gh-muted text-xs mt-0.5">Resets in {countdown}</p>
            )}
            {progress && (
              <p className="text-gh-muted text-xs mt-2">
                Progress saved — will continue from {progress.stage}
              </p>
            )}
            <button
              onClick={handleResume}
              disabled={resuming}
              className="mt-3 px-4 py-1.5 rounded bg-gh-amber/20 text-gh-amber hover:bg-gh-amber/30 transition-colors text-xs font-medium disabled:opacity-50"
            >
              {resuming ? 'Resuming…' : 'Resume Now'}
            </button>
          </div>
        ) : (
          <>
            {/* Progress bar */}
            <div className="h-1.5 rounded-full bg-gh-border overflow-hidden mb-3">
              <div
                className="h-full bg-gh-accent transition-all duration-500 ease-out"
                style={{ width: percent !== null ? `${percent}%` : '0%' }}
              />
            </div>

            <div className="flex justify-between items-center">
              <p className="text-gh-muted text-xs">
                {progress?.message ?? 'Connecting…'}
              </p>
              {percent !== null && (
                <p className="text-gh-muted text-xs tabular-nums">
                  {progress?.current} / {progress?.total}
                </p>
              )}
            </div>

            {sseError && (
              <p className="text-gh-danger text-xs mt-2">{sseError}</p>
            )}
          </>
        )}
      </div>
    </div>
  )
}
