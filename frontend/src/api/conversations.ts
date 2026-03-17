import { apiFetch } from './client'
import type { Conversation, Message } from '../types'

export const conversationsApi = {
  list: (repoId: string) =>
    apiFetch<Conversation[]>(`/api/repos/${repoId}/conversations`),

  create: (repoId: string) =>
    apiFetch<Conversation>(`/api/repos/${repoId}/conversations`, {
      method: 'POST',
    }),

  delete: (id: string) =>
    apiFetch<{ ok: boolean }>(`/api/conversations/${id}`, {
      method: 'DELETE',
    }),

  messages: (id: string) =>
    apiFetch<Message[]>(`/api/conversations/${id}/messages`),

  updateModel: (id: string, provider: string | null, model: string | null) =>
    apiFetch<Conversation>(`/api/conversations/${id}`, {
      method: 'PATCH',
      body: JSON.stringify({ llm_provider: provider, llm_model: model }),
    }),
}
