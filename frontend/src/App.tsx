import { useCallback, useEffect, useState } from 'react'
import { Header } from './components/layout/Header'
import { Sidebar } from './components/layout/Sidebar'
import { RepoInput } from './components/repo/RepoInput'
import { ProgressBar } from './components/repo/ProgressBar'
import { ChatWindow } from './components/chat/ChatWindow'
import { SettingsModal } from './components/settings/SettingsModal'
import { RateLimitBanner } from './components/common/RateLimitBanner'
import { ClaudeCodeLimitPopup } from './components/common/ClaudeCodeLimitPopup'
import { ErrorToast } from './components/common/ErrorToast'
import { reposApi } from './api/repositories'
import { conversationsApi } from './api/conversations'
import { useRateLimits } from './hooks/useRateLimits'
import { useClaudeCodeStatus } from './hooks/useClaudeCodeStatus'
import { useSettings } from './hooks/useSettings'
import type { Repository, Conversation } from './types'

export default function App() {
  const [repos, setRepos] = useState<Repository[]>([])
  const [conversations, setConversations] = useState<Record<string, Conversation[]>>({})
  const [selectedRepoId, setSelectedRepoId] = useState<string | null>(null)
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showRepoInput, setShowRepoInput] = useState(false)
  const [ingestLoading, setIngestLoading] = useState(false)
  const [reposLoading, setReposLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const { limits, isPaused } = useRateLimits()
  const { settings } = useSettings()
  const isClaudeCodeProvider = settings?.llm_provider === 'claude_code'
  const claudeCodeStatus = useClaudeCodeStatus(isClaudeCodeProvider)
  const [claudeCodeLimitDismissed, setClaudeCodeLimitDismissed] = useState(false)

  // Auto-dismiss the popup when limit resets
  const claudeCodeLimited = claudeCodeStatus?.rate_limit?.status !== undefined &&
    claudeCodeStatus.rate_limit.status !== 'allowed'

  useEffect(() => {
    if (!claudeCodeLimited) setClaudeCodeLimitDismissed(false)
  }, [claudeCodeLimited])

  const showError = (msg: string) => setErrorMessage(msg)

  const loadRepos = useCallback(async () => {
    try {
      const data = await reposApi.list()
      setRepos(data)
    } catch {
      // Silent on background refresh — user sees the current list
    } finally {
      setReposLoading(false)
    }
  }, [])

  useEffect(() => { loadRepos() }, [loadRepos])

  const loadConversations = useCallback(async (repoId: string) => {
    try {
      const data = await conversationsApi.list(repoId)
      setConversations(prev => ({ ...prev, [repoId]: data }))
    } catch {
      // Silent
    }
  }, [])

  const selectedRepo = repos.find(r => r.id === selectedRepoId) ?? null
  const selectedConv =
    selectedRepoId
      ? (conversations[selectedRepoId] ?? []).find(c => c.id === selectedConvId) ?? null
      : null

  const handleSelectRepo = (repo: Repository) => {
    setSelectedRepoId(repo.id)
    setSelectedConvId(null)
    setShowRepoInput(false)
    loadConversations(repo.id)
  }

  const handleSelectConv = (conv: Conversation) => {
    setSelectedConvId(conv.id)
    setShowRepoInput(false)
  }

  const handleNewConv = async (repoId: string) => {
    try {
      const conv = await conversationsApi.create(repoId)
      await loadConversations(repoId)
      setSelectedRepoId(repoId)
      setSelectedConvId(conv.id)
      setShowRepoInput(false)
    } catch {
      showError('Failed to create conversation. Please try again.')
    }
  }

  const handleDeleteRepo = async (repoId: string) => {
    try {
      await reposApi.delete(repoId)
      if (selectedRepoId === repoId) {
        setSelectedRepoId(null)
        setSelectedConvId(null)
      }
      await loadRepos()
    } catch {
      showError('Failed to delete repository. Please try again.')
    }
  }

  const handleModelChange = (updated: Conversation) => {
    setConversations(prev => ({
      ...prev,
      [updated.repository_id]: (prev[updated.repository_id] ?? []).map(c =>
        c.id === updated.id ? updated : c
      ),
    }))
    setSelectedConvId(updated.id)
  }

  const handleDeleteConv = async (convId: string) => {
    try {
      await conversationsApi.delete(convId)
      if (selectedConvId === convId) setSelectedConvId(null)
      if (selectedRepoId) await loadConversations(selectedRepoId)
    } catch {
      showError('Failed to delete conversation. Please try again.')
    }
  }

  const handleIngest = async (url: string) => {
    setIngestLoading(true)
    try {
      const repo = await reposApi.ingest(url)
      await loadRepos()
      setSelectedRepoId(repo.id)
      setSelectedConvId(null)
      setShowRepoInput(false)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to start ingestion.'
      showError(msg)
    } finally {
      setIngestLoading(false)
    }
  }

  const handleProgressComplete = async () => {
    await loadRepos()
    if (selectedRepoId) await loadConversations(selectedRepoId)
  }

  const renderMain = () => {
    if (showRepoInput || !selectedRepo) {
      return <RepoInput onSubmit={handleIngest} loading={ingestLoading} />
    }

    if (['pending', 'ingesting', 'syncing', 'paused'].includes(selectedRepo.status)) {
      return (
        <ProgressBar
          repoId={selectedRepo.id}
          repoName={selectedRepo.name}
          onComplete={handleProgressComplete}
        />
      )
    }

    if (selectedConv && selectedRepo.status === 'ready') {
      return (
        <ChatWindow
          conversation={selectedConv}
          repoName={selectedRepo.name}
          globalSettings={settings}
          onModelChange={handleModelChange}
        />
      )
    }

    // Repo ready but no conversation selected
    return (
      <div className="flex-1 flex items-center justify-center px-8">
        <div className="text-center">
          <p className="text-gh-text font-medium mb-1">{selectedRepo.name}</p>
          <p className="text-gh-muted text-sm mb-4">
            {selectedRepo.status === 'ready'
              ? 'Select a conversation or start a new one.'
              : 'Repository ingestion failed.'}
          </p>
          {selectedRepo.status === 'ready' && (
            <button
              onClick={() => handleNewConv(selectedRepo.id)}
              className="px-4 py-2 rounded-lg bg-gh-accent text-gh-bg text-sm font-medium hover:bg-gh-accent/90 transition-colors"
            >
              New Conversation
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="h-screen flex flex-col bg-gh-bg text-gh-text overflow-hidden">
      <Header onSettings={() => setShowSettings(true)} />

      {isPaused &&
        Object.entries(limits)
          .filter(([, v]) => v.is_paused)
          .map(([service, status]) => (
            <RateLimitBanner key={service} service={service} status={status} />
          ))}

      <div className="flex flex-1 min-h-0">
        <Sidebar
          repos={repos}
          conversations={conversations}
          selectedRepoId={selectedRepoId}
          selectedConvId={selectedConvId}
          loading={reposLoading}
          onSelectRepo={handleSelectRepo}
          onSelectConv={handleSelectConv}
          onNewConv={handleNewConv}
          onDeleteRepo={handleDeleteRepo}
          onDeleteConv={handleDeleteConv}
          onNewRepo={() => {
            setShowRepoInput(true)
            setSelectedRepoId(null)
            setSelectedConvId(null)
          }}
        />

        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          {renderMain()}
        </main>
      </div>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
      {isClaudeCodeProvider && claudeCodeLimited && !claudeCodeLimitDismissed && (
        <ClaudeCodeLimitPopup
          resetsAt={claudeCodeStatus?.rate_limit?.resets_at ?? null}
          onOpenSettings={() => { setClaudeCodeLimitDismissed(true); setShowSettings(true) }}
          onDismiss={() => setClaudeCodeLimitDismissed(true)}
        />
      )}
      {errorMessage && (
        <ErrorToast message={errorMessage} onDismiss={() => setErrorMessage(null)} />
      )}
    </div>
  )
}
