import { useState } from 'react'
import { reposApi } from '../../api/repositories'

interface Props {
  diff: string
  repoId: string
  patchReady: boolean
}

export function DiffViewer({ diff, repoId, patchReady }: Props) {
  const [copied, setCopied] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<{
    success: boolean
    message: string
  } | null>(null)

  const lines = diff.split('\n')

  const copy = async () => {
    await navigator.clipboard.writeText(diff)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const download = () => {
    const blob = new Blob([diff], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'changes.patch'
    a.click()
    URL.revokeObjectURL(url)
  }

  const applyPatch = async () => {
    setApplying(true)
    setApplyResult(null)
    try {
      const result = await reposApi.applyPatch(repoId, diff)
      if (result.success) {
        const fileList = result.files_changed.join(', ')
        setApplyResult({
          success: true,
          message: `Applied to ${result.files_changed.length} file(s): ${fileList} (commit ${result.commit_hash?.slice(0, 7)})`,
        })
      } else {
        setApplyResult({ success: false, message: result.error ?? 'Patch failed.' })
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Failed to apply patch.'
      setApplyResult({ success: false, message: msg })
    } finally {
      setApplying(false)
    }
  }

  return (
    <div className="rounded-lg overflow-hidden border border-gh-border bg-gh-surface my-2">
      {/* Header — label only */}
      <div className="px-3 py-1.5 bg-gh-border/30 border-b border-gh-border">
        <span className="text-gh-muted text-xs font-mono">diff</span>
      </div>

      {/* Diff lines */}
      <pre className="overflow-x-auto p-2 text-xs font-mono leading-relaxed">
        {lines.map((line, i) => {
          let cls = 'text-gh-muted'
          if (line.startsWith('+') && !line.startsWith('+++')) {
            cls = 'bg-gh-success/10 text-gh-success'
          } else if (line.startsWith('-') && !line.startsWith('---')) {
            cls = 'bg-gh-danger/10 text-gh-danger'
          } else if (line.startsWith('@@')) {
            cls = 'text-gh-accent bg-gh-accent/5'
          } else if (line.startsWith('+++') || line.startsWith('---')) {
            cls = 'text-gh-text font-medium'
          } else if (line.startsWith('diff ')) {
            cls = 'text-gh-text font-medium'
          }

          return (
            <div key={i} className={`px-2 py-0.5 rounded ${cls}`}>
              {line || ' '}
            </div>
          )
        })}
      </pre>

      {/* Footer — action buttons */}
      <div className="flex items-center justify-end px-3 py-1.5 bg-gh-border/30 border-t border-gh-border gap-2">
        <button
          onClick={copy}
          className="text-gh-muted hover:text-gh-text text-xs transition-colors"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
        <button
          onClick={download}
          className="text-gh-accent hover:text-gh-accent/80 text-xs transition-colors"
        >
          Download .patch
        </button>
        <button
          onClick={applyPatch}
          disabled={!patchReady || applying}
          title={
            !patchReady
              ? 'Working clone not ready yet — please wait a moment after ingestion'
              : 'Apply this patch to your local working clone'
          }
          className={`text-xs px-2 py-0.5 rounded transition-colors ${
            !patchReady || applying
              ? 'text-gh-muted cursor-not-allowed opacity-50'
              : 'bg-gh-success/20 text-gh-success hover:bg-gh-success/30'
          }`}
        >
          {applying ? 'Applying…' : 'Apply Patch'}
        </button>
      </div>

      {/* Apply result banner */}
      {applyResult && (
        <div
          className={`px-3 py-1.5 text-xs border-t border-gh-border ${
            applyResult.success
              ? 'bg-gh-success/10 text-gh-success'
              : 'bg-gh-danger/10 text-gh-danger'
          }`}
        >
          {applyResult.success ? '✓ ' : '✗ '}
          {applyResult.message}
        </div>
      )}
    </div>
  )
}
