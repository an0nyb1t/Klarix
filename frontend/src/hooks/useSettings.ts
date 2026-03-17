import { useCallback, useEffect, useState } from 'react'
import { settingsApi } from '../api/settings'
import type { Settings, SettingsUpdate } from '../types'

export function useSettings() {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const data = await settingsApi.get()
      setSettings(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load settings')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const save = useCallback(async (updates: SettingsUpdate) => {
    setSaving(true)
    setError(null)
    try {
      await settingsApi.update(updates)
      await load()
      return true
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save settings')
      return false
    } finally {
      setSaving(false)
    }
  }, [load])

  return { settings, loading, saving, error, save, reload: load }
}
