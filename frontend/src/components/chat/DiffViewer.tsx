import { useState } from 'react'

interface Props {
  diff: string
}

export function DiffViewer({ diff }: Props) {
  const [copied, setCopied] = useState(false)

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

  return (
    <div className="rounded-lg overflow-hidden border border-gh-border bg-gh-surface my-2">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-gh-border/30 border-b border-gh-border">
        <span className="text-gh-muted text-xs font-mono">diff</span>
        <div className="flex items-center gap-2">
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
        </div>
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
    </div>
  )
}
