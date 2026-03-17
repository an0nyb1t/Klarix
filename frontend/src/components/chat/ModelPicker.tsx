import { useState } from 'react'
import { conversationsApi } from '../../api/conversations'
import type { Conversation, Settings } from '../../types'

interface Props {
  conversation: Conversation
  globalSettings: Settings | null
  onModelChange: (updated: Conversation) => void
}

// Models per provider
const PROVIDER_MODELS: Record<string, string[]> = {
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5-20251001'],
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  claude_code: ['opus', 'sonnet', 'haiku'],
}

// Clean display names for models
const DISPLAY_NAMES: Record<string, string> = {
  'claude-opus-4-6': 'Claude Opus 4.6',
  'claude-sonnet-4-6': 'Claude Sonnet 4.6',
  'claude-haiku-4-5-20251001': 'Claude Haiku 4.5',
  'gpt-4o': 'GPT-4o',
  'gpt-4o-mini': 'GPT-4o Mini',
  'gpt-4-turbo': 'GPT-4 Turbo',
  'gpt-3.5-turbo': 'GPT-3.5 Turbo',
  opus: 'Opus 4.6',
  sonnet: 'Sonnet 4.6',
  haiku: 'Haiku 4.5',
}

function displayName(model: string): string {
  return DISPLAY_NAMES[model] ?? model
}

export function ModelPicker({ conversation, globalSettings, onModelChange }: Props) {
  const [saving, setSaving] = useState(false)
  const [savedLabel, setSavedLabel] = useState(false)

  // Resolve the effective provider and model
  const effectiveProvider = conversation.llm_provider ?? globalSettings?.llm_provider ?? ''
  const effectiveModel = conversation.llm_model ?? globalSettings?.llm_model ?? ''

  // Only show models for the active provider
  const models = PROVIDER_MODELS[effectiveProvider] ?? []

  const handleChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
    const newModel = e.target.value

    // If user picks the same model as global, clear the override
    const isGlobalDefault =
      newModel === globalSettings?.llm_model && effectiveProvider === globalSettings?.llm_provider
    const provider = isGlobalDefault ? null : effectiveProvider
    const model = isGlobalDefault ? null : newModel

    setSaving(true)
    try {
      const updated = await conversationsApi.updateModel(conversation.id, provider, model)
      onModelChange(updated)
      setSavedLabel(true)
      setTimeout(() => setSavedLabel(false), 2000)
    } catch {
      // Silently ignore — user can retry
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {conversation.has_summary && (
        <span className="text-gh-muted text-xs">context compressed</span>
      )}
      {savedLabel && (
        <span className="text-green-400 text-xs">Model updated</span>
      )}
      <select
        value={effectiveModel}
        onChange={handleChange}
        disabled={saving || models.length === 0}
        className="
          text-xs bg-gh-surface border border-gh-border rounded px-2 py-1
          text-gh-text focus:outline-none focus:border-gh-accent
          disabled:opacity-50 cursor-pointer
        "
      >
        {models.map(m => (
          <option key={m} value={m}>
            {displayName(m)}
          </option>
        ))}
      </select>
    </div>
  )
}
