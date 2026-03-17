import { useState } from 'react'
import type { Repository, Conversation } from '../../types'

interface Props {
  repos: Repository[]
  conversations: Record<string, Conversation[]>
  selectedRepoId: string | null
  selectedConvId: string | null
  loading?: boolean
  onSelectRepo: (repo: Repository) => void
  onSelectConv: (conv: Conversation) => void
  onNewConv: (repoId: string) => void
  onDeleteRepo: (repoId: string) => void
  onDeleteConv: (convId: string) => void
  onNewRepo: () => void
}

const STATUS_DOT: Record<string, string> = {
  pending: 'bg-gh-muted animate-pulse',
  ready: 'bg-gh-success',
  ingesting: 'bg-gh-accent animate-pulse',
  syncing: 'bg-gh-accent animate-pulse',
  paused: 'bg-gh-amber',
  failed: 'bg-gh-danger',
}

export function Sidebar({
  repos,
  conversations,
  selectedRepoId,
  selectedConvId,
  loading = false,
  onSelectRepo,
  onSelectConv,
  onNewConv,
  onDeleteRepo,
  onDeleteConv,
  onNewRepo,
}: Props) {
  const [hoveredRepo, setHoveredRepo] = useState<string | null>(null)
  const [hoveredConv, setHoveredConv] = useState<string | null>(null)

  return (
    <aside className="w-56 flex flex-col bg-gh-surface border-r border-gh-border shrink-0 overflow-hidden">
      {/* New Repo button */}
      <div className="px-3 py-2.5 border-b border-gh-border">
        <button
          onClick={onNewRepo}
          className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded border border-dashed border-gh-border text-gh-muted hover:text-gh-text hover:border-gh-accent/50 transition-colors text-xs font-medium"
        >
          <span className="text-base leading-none">+</span>
          <span>Add Repository</span>
        </button>
      </div>

      {/* Repo list */}
      <nav className="flex-1 overflow-y-auto py-1">
        {loading && (
          <div className="px-3 py-2 space-y-2">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-6 rounded bg-gh-border/40 animate-pulse" />
            ))}
          </div>
        )}
        {!loading && repos.length === 0 && (
          <p className="px-4 py-6 text-center text-gh-muted text-xs">No repositories yet</p>
        )}
        {!loading && repos.map(repo => {
          const isSelected = repo.id === selectedRepoId
          const dotColor = STATUS_DOT[repo.status] ?? 'bg-gh-muted'
          const convList = conversations[repo.id] ?? []

          return (
            <div key={repo.id}>
              {/* Repo row */}
              <div
                className={`group flex items-center gap-2 px-3 py-2 cursor-pointer transition-colors ${
                  isSelected ? 'bg-gh-accent/10 text-gh-text' : 'text-gh-muted hover:text-gh-text hover:bg-white/5'
                }`}
                onMouseEnter={() => setHoveredRepo(repo.id)}
                onMouseLeave={() => setHoveredRepo(null)}
                onClick={() => onSelectRepo(repo)}
              >
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dotColor}`} />
                <span className="flex-1 truncate text-xs font-medium">{repo.name}</span>
                {hoveredRepo === repo.id && (
                  <button
                    onClick={e => {
                      e.stopPropagation()
                      if (window.confirm(`Delete "${repo.name}" and all its conversations?`)) {
                        onDeleteRepo(repo.id)
                      }
                    }}
                    className="text-gh-muted hover:text-gh-danger transition-colors text-xs"
                    title="Delete repository"
                  >
                    x
                  </button>
                )}
              </div>

              {/* Conversations for this repo */}
              {isSelected && (
                <div className="pb-1">
                  {convList.map(conv => (
                    <div
                      key={conv.id}
                      className={`group flex items-center gap-2 pl-6 pr-3 py-1.5 cursor-pointer transition-colors ${
                        conv.id === selectedConvId
                          ? 'bg-white/5 text-gh-text'
                          : 'text-gh-muted hover:text-gh-text hover:bg-white/5'
                      }`}
                      onMouseEnter={() => setHoveredConv(conv.id)}
                      onMouseLeave={() => setHoveredConv(null)}
                      onClick={() => onSelectConv(conv)}
                    >
                      <span className="text-gh-muted text-xs">›</span>
                      <span className="flex-1 truncate text-xs">{conv.title}</span>
                      {hoveredConv === conv.id && (
                        <button
                          onClick={e => {
                            e.stopPropagation()
                            if (window.confirm(`Delete conversation "${conv.title}"?`)) {
                              onDeleteConv(conv.id)
                            }
                          }}
                          className="text-gh-muted hover:text-gh-danger transition-colors text-xs"
                          title="Delete conversation"
                        >
                          x
                        </button>
                      )}
                    </div>
                  ))}

                  {repo.status === 'ready' && (
                    <button
                      onClick={() => onNewConv(repo.id)}
                      className="flex items-center gap-1.5 pl-6 pr-3 py-1.5 w-full text-left text-gh-muted hover:text-gh-accent transition-colors text-xs"
                    >
                      <span>+</span>
                      <span>New conversation</span>
                    </button>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </nav>
    </aside>
  )
}
