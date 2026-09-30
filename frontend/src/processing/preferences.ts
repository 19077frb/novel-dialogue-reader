import { useSyncExternalStore } from 'react'

export const PROCESSING_PREFERENCES_KEY = 'ndr:processing-preferences:v1'
export interface ProcessingPreferences {
  profileId: string
  concurrency: number
  maxRechecks: number
  tokenLimit: number | null
  maxOutputTokens: number | null
}
const defaults: ProcessingPreferences = {
  profileId: '', concurrency: 2, maxRechecks: 0, tokenLimit: null, maxOutputTokens: null,
}
let cachedRaw: string | null | undefined
let cached = defaults
let fallbackRaw: string | null = null
let storageUnavailable = false
const listeners = new Set<() => void>()

function positive(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : null
}
function normalize(value: Partial<ProcessingPreferences>): ProcessingPreferences {
  return {
    profileId: typeof value.profileId === 'string' ? value.profileId : '',
    concurrency: Math.min(16, positive(value.concurrency) ?? 2),
    maxRechecks: typeof value.maxRechecks === 'number' && Number.isSafeInteger(value.maxRechecks)
      && value.maxRechecks >= 0 ? value.maxRechecks : 0,
    tokenLimit: positive(value.tokenLimit),
    maxOutputTokens: positive(value.maxOutputTokens),
  }
}
export function getProcessingPreferences(): ProcessingPreferences {
  let raw = fallbackRaw
  if (!storageUnavailable) {
    try { raw = localStorage.getItem(PROCESSING_PREFERENCES_KEY) } catch { storageUnavailable = true }
  }
  if (raw !== cachedRaw) {
    cachedRaw = raw
    try {
      const parsed: unknown = JSON.parse(raw ?? '{}')
      cached = parsed && typeof parsed === 'object' ? normalize(parsed) : defaults
    } catch { cached = defaults }
  }
  return cached
}
export function updateProcessingPreferences(patch: Partial<ProcessingPreferences>) {
  fallbackRaw = JSON.stringify(normalize({ ...getProcessingPreferences(), ...patch }))
  try { localStorage.setItem(PROCESSING_PREFERENCES_KEY, fallbackRaw) } catch { storageUnavailable = true }
  listeners.forEach(listener => listener())
}
function subscribe(listener: () => void) {
  listeners.add(listener)
  const changed = (event: StorageEvent) => {
    if (event.key === PROCESSING_PREFERENCES_KEY || event.key === null) listener()
  }
  window.addEventListener('storage', changed)
  return () => { listeners.delete(listener); window.removeEventListener('storage', changed) }
}
export function useProcessingPreferences() {
  return [useSyncExternalStore(subscribe, getProcessingPreferences), updateProcessingPreferences] as const
}
