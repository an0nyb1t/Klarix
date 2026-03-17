export interface Repository {
  id: string
  name: string
  status: 'pending' | 'ingesting' | 'ready' | 'failed' | 'syncing' | 'paused'
  total_commits: number
  total_files?: number
  default_branch?: string
  last_synced_at?: string
  metadata?: Record<string, unknown>
}

export interface Conversation {
  id: string
  repository_id: string
  title: string
  created_at: string
  message_count: number
  llm_provider: string | null  // V1.2 — null means "using global setting"
  llm_model: string | null     // V1.2 — null means "using global setting"
  has_summary: boolean         // V1.2 — true when earlier messages are compressed
}

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  has_diff: boolean
  created_at: string
}

export interface Settings {
  llm_provider: string
  llm_model: string
  llm_base_url: string
  github_token_set: boolean
  embedding_model: string
  llm_rate_limit_tpm: number
  claude_code_available: boolean
}

export interface ClaudeCodeStatus {
  available: boolean
  version: string | null
  authenticated: boolean
  rate_limit: {
    status: string
    resets_at: string | null
    rate_limit_type: string
    last_checked_at: string | null
  } | null
  error: string | null
}

export interface SettingsUpdate {
  llm_provider?: string
  llm_model?: string
  llm_base_url?: string
  llm_api_key?: string
  github_token?: string
  llm_rate_limit_tpm?: number
}

export interface RateLimitStatus {
  limit_max: number
  limit_remaining: number
  usage_percent: number
  resets_at: string | null
  is_paused: boolean
}

export interface CheckpointInfo {
  operation: string
  stage: string
  progress_current: number
  progress_total: number
  paused_reason: string | null
  resets_at: string | null
  paused_at: string
}

export type WsMessage =
  | { type: 'chunk'; content: string }
  | { type: 'done'; message_id: string }
  | { type: 'error'; message: string }
  | { type: 'rate_limited'; message: string; resets_at: string | null }

export interface ProgressEvent {
  stage: string
  current: number
  total: number
  message: string
}

export interface PausedEvent {
  reason: string
  usage_percent: number | null
  resets_at: string | null
  message: string
}

// UI state for a streaming message being built up
export interface StreamingMessage {
  role: 'assistant'
  content: string
  isStreaming: boolean
}
