import { useEffect, useState } from 'react'
import { useSettings } from '../../hooks/useSettings'
import { settingsApi } from '../../api/settings'
import type { SettingsUpdate } from '../../types'

interface Props {
  onClose: () => void
}

const PROVIDERS = ['anthropic', 'openai', 'ollama', 'custom', 'claude_code'] as const

const PROVIDER_LABELS: Record<string, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  ollama: 'Ollama',
  custom: 'Custom',
  claude_code: 'Claude Code',
}

const PROVIDER_MODELS: Record<string, string[]> = {
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'o1', 'o1-mini'],
  ollama: [],
  custom: [],
  claude_code: ['sonnet', 'opus', 'haiku'],
}

export function SettingsModal({ onClose }: Props) {
  const { settings, saving, error: saveError, save } = useSettings()

  const [provider, setProvider] = useState('anthropic')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [githubToken, setGithubToken] = useState('')
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle')
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const [ghTestStatus, setGhTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle')
  const [ghTestMsg, setGhTestMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!settings) return
    setProvider(settings.llm_provider)
    setModel(settings.llm_model)
    setBaseUrl(settings.llm_base_url ?? '')
  }, [settings])

  const handleSave = async () => {
    const updates: SettingsUpdate = { llm_provider: provider, llm_model: model }
    if (apiKey) updates.llm_api_key = apiKey
    if (baseUrl) updates.llm_base_url = baseUrl
    if (githubToken) updates.github_token = githubToken
    const ok = await save(updates)
    if (ok) onClose()
  }

  const handleTestGithub = async () => {
    setGhTestStatus('testing')
    setGhTestMsg(null)
    try {
      const result = await settingsApi.testGithub(githubToken)
      setGhTestStatus(result.ok ? 'ok' : 'fail')
      setGhTestMsg(result.message)
    } catch (e) {
      setGhTestStatus('fail')
      setGhTestMsg(e instanceof Error ? e.message : 'Connection failed')
    }
  }

  const handleTest = async () => {
    setTestStatus('testing')
    setTestMsg(null)
    try {
      const result = await settingsApi.testLlm(
        provider,
        model,
        baseUrl || undefined,
        apiKey || undefined,
      )
      setTestStatus('ok')
      setTestMsg(result.message)
    } catch (e) {
      setTestStatus('fail')
      setTestMsg(e instanceof Error ? e.message : 'Connection failed')
    }
  }

  const models = PROVIDER_MODELS[provider] ?? []
  const isClaudeCode = provider === 'claude_code'
  const showApiKey = !isClaudeCode && provider !== 'ollama'
  const showBaseUrl = !isClaudeCode && (provider === 'ollama' || provider === 'custom')

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="w-full max-w-md bg-gh-bg border border-gh-border rounded-xl shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gh-border">
          <h2 className="text-gh-text font-semibold">Settings</h2>
          <button
            onClick={onClose}
            className="text-gh-muted hover:text-gh-text transition-colors text-xl leading-none"
          >
            x
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* Provider */}
          <div>
            <label className="block text-gh-muted text-xs font-medium mb-1.5">LLM Provider</label>
            <select
              value={provider}
              onChange={e => { setProvider(e.target.value); setModel('') }}
              className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm focus:outline-none focus:border-gh-accent/50 transition-colors"
            >
              {PROVIDERS.map(p => (
                <option key={p} value={p}>
                  {PROVIDER_LABELS[p] ?? p}
                </option>
              ))}
            </select>
          </div>

          {/* Claude Code status indicator */}
          {isClaudeCode && (
            <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
              settings?.claude_code_available
                ? 'border-gh-success/30 bg-gh-success/5 text-gh-success'
                : 'border-gh-danger/30 bg-gh-danger/5 text-gh-danger'
            }`}>
              {settings?.claude_code_available
                ? 'Claude Code CLI detected'
                : 'Claude Code CLI not found — install with: npm install -g @anthropic-ai/claude-code'
              }
            </div>
          )}

          {/* Model */}
          <div>
            <label className="block text-gh-muted text-xs font-medium mb-1.5">Model</label>
            {models.length > 0 ? (
              <select
                value={model}
                onChange={e => setModel(e.target.value)}
                className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm focus:outline-none focus:border-gh-accent/50 transition-colors"
              >
                <option value="">Select a model…</option>
                {models.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
            ) : (
              <input
                value={model}
                onChange={e => setModel(e.target.value)}
                placeholder={provider === 'ollama' ? 'llama3.2, codestral, mistral…' : 'Model name'}
                className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm placeholder-gh-muted focus:outline-none focus:border-gh-accent/50 transition-colors"
              />
            )}
          </div>

          {/* API Key */}
          {showApiKey && (
            <div>
              <label className="block text-gh-muted text-xs font-medium mb-1.5">API Key</label>
              <input
                type="password"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="Leave blank to keep existing key"
                className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm placeholder-gh-muted focus:outline-none focus:border-gh-accent/50 transition-colors"
              />
            </div>
          )}

          {/* Base URL */}
          {showBaseUrl && (
            <div>
              <label className="block text-gh-muted text-xs font-medium mb-1.5">Base URL</label>
              <input
                value={baseUrl}
                onChange={e => setBaseUrl(e.target.value)}
                placeholder={provider === 'ollama' ? 'http://localhost:11434' : 'https://your-api.com/v1'}
                className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm placeholder-gh-muted focus:outline-none focus:border-gh-accent/50 transition-colors"
              />
            </div>
          )}

          {/* GitHub Token */}
          <div>
            <label className="block text-gh-muted text-xs font-medium mb-1.5">
              GitHub Token{' '}
              {settings?.github_token_set && (
                <span className="text-gh-success font-normal">set</span>
              )}
            </label>
            <input
              type="password"
              value={githubToken}
              onChange={e => setGithubToken(e.target.value)}
              placeholder="Leave blank to keep existing token"
              className="w-full bg-gh-surface border border-gh-border rounded-lg px-3 py-2 text-gh-text text-sm placeholder-gh-muted focus:outline-none focus:border-gh-accent/50 transition-colors"
            />
            <div className="flex items-center gap-3 mt-2">
              <button
                onClick={handleTestGithub}
                disabled={ghTestStatus === 'testing' || (!githubToken && !settings?.github_token_set)}
                className="px-3 py-1.5 rounded-lg bg-gh-surface border border-gh-border text-gh-text text-xs hover:border-gh-accent/50 transition-colors disabled:opacity-40"
              >
                {ghTestStatus === 'testing' ? 'Testing...' : 'Test Token'}
              </button>
              {ghTestStatus === 'ok' && (
                <span className="text-gh-success text-xs">{ghTestMsg}</span>
              )}
              {ghTestStatus === 'fail' && (
                <span className="text-gh-danger text-xs">{ghTestMsg}</span>
              )}
            </div>
            <p className="text-gh-muted text-xs mt-1">
              Required for private repos and higher API rate limits.
            </p>
          </div>

          {/* Test connection */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleTest}
              disabled={testStatus === 'testing' || (!model && !isClaudeCode)}
              className="px-3 py-1.5 rounded-lg bg-gh-surface border border-gh-border text-gh-text text-xs hover:border-gh-accent/50 transition-colors disabled:opacity-40"
            >
              {testStatus === 'testing' ? 'Testing…' : 'Test Connection'}
            </button>
            {testStatus === 'ok' && (
              <span className="text-gh-success text-xs">{testMsg}</span>
            )}
            {testStatus === 'fail' && (
              <span className="text-gh-danger text-xs">{testMsg}</span>
            )}
          </div>

          {saveError && <p className="text-gh-danger text-xs">{saveError}</p>}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gh-border">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gh-muted text-sm hover:text-gh-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 rounded-lg bg-gh-accent text-gh-bg text-sm font-medium hover:bg-gh-accent/90 transition-colors disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
