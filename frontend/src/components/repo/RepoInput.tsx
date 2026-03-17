import { FormEvent, useState } from 'react'

interface Props {
  onSubmit: (url: string) => Promise<void>
  loading?: boolean
}

function isGitHubUrl(url: string): boolean {
  return /^https?:\/\/(www\.)?github\.com\/[\w.-]+\/[\w.-]/.test(url.trim())
}

export function RepoInput({ onSubmit, loading }: Props) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)

    const trimmed = url.trim()
    if (!trimmed) return

    if (!isGitHubUrl(trimmed)) {
      setError('Please enter a valid GitHub repository URL (e.g. https://github.com/owner/repo)')
      return
    }

    try {
      await onSubmit(trimmed)
      setUrl('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start ingestion')
    }
  }

  return (
    <div className="flex-1 flex items-center justify-center px-8">
      <div className="w-full max-w-lg">
        <h1 className="text-2xl font-semibold text-gh-text mb-2">Chat with any GitHub repo</h1>
        <p className="text-gh-muted text-sm mb-6">
          Paste a public or private GitHub repository URL below. GitChat will index
          the code, commits, issues, and PRs — then let you ask anything about it.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-3">
          <input
            type="text"
            value={url}
            onChange={e => setUrl(e.target.value)}
            placeholder="https://github.com/owner/repository"
            disabled={loading}
            className="w-full px-4 py-3 rounded-lg bg-gh-surface border border-gh-border text-gh-text placeholder-gh-muted focus:outline-none focus:border-gh-accent transition-colors text-sm font-mono disabled:opacity-50"
          />

          {error && (
            <p className="text-gh-danger text-xs">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading || !url.trim()}
            className="px-6 py-2.5 rounded-lg bg-gh-accent text-gh-bg font-semibold text-sm hover:bg-gh-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Starting…' : 'Analyze Repository'}
          </button>
        </form>
      </div>
    </div>
  )
}
