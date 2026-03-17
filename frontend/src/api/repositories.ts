import { apiFetch } from './client'
import type { Repository } from '../types'

export const reposApi = {
  list: () => apiFetch<Repository[]>('/api/repos'),

  get: (id: string) => apiFetch<Repository>(`/api/repos/${id}`),

  ingest: (url: string) =>
    apiFetch<Repository>('/api/repos', {
      method: 'POST',
      body: JSON.stringify({ url }),
    }),

  sync: (id: string) =>
    apiFetch<Repository>(`/api/repos/${id}/sync`, { method: 'POST' }),

  delete: (id: string) =>
    apiFetch<{ ok: boolean }>(`/api/repos/${id}`, { method: 'DELETE' }),

  resume: (id: string) =>
    apiFetch<{ id: string; status: string; resumed_from: string }>(
      `/api/repos/${id}/resume`,
      { method: 'POST' },
    ),
}
