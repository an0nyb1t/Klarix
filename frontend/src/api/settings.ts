import { apiFetch } from './client'
import type { ClaudeCodeStatus, Settings, SettingsUpdate } from '../types'

export const settingsApi = {
  get: () => apiFetch<Settings>('/api/settings'),

  update: (data: SettingsUpdate) =>
    apiFetch<{ ok: boolean }>('/api/settings', {
      method: 'PUT',
      body: JSON.stringify(data),
    }),

  testLlm: (provider: string, model: string, baseUrl?: string, apiKey?: string) =>
    apiFetch<{ ok: boolean; message: string }>('/api/settings/test-llm', {
      method: 'POST',
      body: JSON.stringify({ provider, model, base_url: baseUrl ?? '', api_key: apiKey ?? '' }),
    }),

  testGithub: (token: string) =>
    apiFetch<{ ok: boolean; message: string }>('/api/settings/test-github', {
      method: 'POST',
      body: JSON.stringify({ token }),
    }),

  rateLimits: () =>
    apiFetch<Record<string, { limit_max: number; limit_remaining: number; usage_percent: number; resets_at: string | null; is_paused: boolean }>>('/api/rate-limits'),

  claudeCodeStatus: () =>
    apiFetch<ClaudeCodeStatus>('/api/claude-code/status'),
}
