import { useSyncExternalStore } from 'react'
import type { InferenceOptions } from '../api/types'

export const PROCESSING_PREFERENCES_KEY = 'ndr:processing-preferences:v1'
export interface ProcessingPreferences {
  profileId: string
  concurrency: number
  maxRecheckRounds: number
  maxFormatRetries: number
  tokenLimit: number | null
  maxOutputTokens: number | null
  thinkingMode: NonNullable<InferenceOptions['thinking_mode']>
  thinkingEffort: NonNullable<InferenceOptions['reasoning_effort']>
}
const defaults: ProcessingPreferences = {
  profileId: '', concurrency: 2, maxRecheckRounds: 0, maxFormatRetries: 1, tokenLimit: null, maxOutputTokens: null,
  thinkingMode: 'default', thinkingEffort: 'default',
}
export function getDefaultProcessingPreferences(): ProcessingPreferences {
  return { ...defaults }
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
    maxRecheckRounds: typeof value.maxRecheckRounds === 'number' && Number.isSafeInteger(value.maxRecheckRounds)
      && value.maxRecheckRounds >= 0 ? value.maxRecheckRounds : 0,
    maxFormatRetries: typeof value.maxFormatRetries === 'number' && Number.isSafeInteger(value.maxFormatRetries)
      && value.maxFormatRetries >= 0 ? Math.min(5, value.maxFormatRetries) : 1,
    tokenLimit: positive(value.tokenLimit),
    maxOutputTokens: positive(value.maxOutputTokens),
    thinkingMode: ['default', 'disabled', 'enabled', 'adaptive'].includes(value.thinkingMode ?? '') ? value.thinkingMode! : 'default',
    thinkingEffort: ['default', 'low', 'medium', 'high'].includes(value.thinkingEffort ?? '') ? value.thinkingEffort! : 'default',
  }
}
export function inferenceOptions(preferences: ProcessingPreferences): InferenceOptions | undefined {
  if (preferences.thinkingMode === 'default' && preferences.thinkingEffort === 'default') return undefined
  return { thinking_mode: preferences.thinkingMode, reasoning_effort: preferences.thinkingEffort }
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
