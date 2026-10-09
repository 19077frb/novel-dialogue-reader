import { useSyncExternalStore } from 'react'
import type { InferenceOptions } from '../api/types'
import type { DialogueStrategy } from '../api/jobs'
import { getGeneralSettings, subscribeGeneralSettings } from '../settings/preferences'

export const PROCESSING_PREFERENCES_KEY = 'ndr:processing-preferences:v1'
export interface ProcessingPreferences {
  rosterRepairEnabled: boolean
  maxRosterRepairs: number
  dialogueStrategy: DialogueStrategy
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
  rosterRepairEnabled: false, maxRosterRepairs: 1,
  dialogueStrategy: 'legacy',
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
    rosterRepairEnabled: value.rosterRepairEnabled === true,
    maxRosterRepairs: typeof value.maxRosterRepairs === 'number' && Number.isSafeInteger(value.maxRosterRepairs)
      && value.maxRosterRepairs >= 0 ? Math.min(5, value.maxRosterRepairs) : 1,
    dialogueStrategy: ['complete', 'complete-review', 'complete-blocks', 'complete-blocks-review', 'complete-blocks-isolated', 'complete-blocks-isolated-review', 'complete-blocks-isolated-feedback-review'].includes(value.dialogueStrategy ?? '') ? value.dialogueStrategy! : 'legacy',
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
/** Old persisted execution snapshots must not enable a new paid pipeline. */
export function rosterRepairOptions(preferences: ProcessingPreferences) {
  return preferences.rosterRepairEnabled === true ? {
    rosterRepairEnabled: true, maxRosterRepairs: preferences.maxRosterRepairs ?? 1,
    maxFormatRetries: preferences.maxFormatRetries ?? 1,
  } : {}
}
/** Heuristic only: reasoning/output/error feedback may cost more than this estimate. */
export function estimateRosterTokens(characters: number, preferences: ProcessingPreferences): number {
  const base = Math.max(1, characters) + 2000
  return preferences.rosterRepairEnabled === true
    ? base * (1 + (preferences.maxRosterRepairs ?? 1) + (preferences.maxFormatRetries ?? 1)) : base
}
export function getStoredProcessingPreferences(): ProcessingPreferences {
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
let effectiveSource: ProcessingPreferences | undefined
let effectiveEnabled: boolean | undefined
let effectiveCache = defaults
/** Only new workflows use these effective preferences; frozen task inputs remain intact. */
export function getProcessingPreferences(): ProcessingPreferences {
  const source = getStoredProcessingPreferences()
  const enabled = getGeneralSettings().enableExperimentalFeatures
  if (source !== effectiveSource || enabled !== effectiveEnabled) {
    effectiveSource = source; effectiveEnabled = enabled
    effectiveCache = enabled ? source : { ...source, dialogueStrategy: 'legacy', rosterRepairEnabled: false }
  }
  return effectiveCache
}
export function updateProcessingPreferences(patch: Partial<ProcessingPreferences>) {
  fallbackRaw = JSON.stringify(normalize({ ...getStoredProcessingPreferences(), ...patch }))
  try { localStorage.setItem(PROCESSING_PREFERENCES_KEY, fallbackRaw) } catch { storageUnavailable = true }
  listeners.forEach(listener => listener())
}
function subscribe(listener: () => void) {
  listeners.add(listener)
  const unsubscribeGeneral = subscribeGeneralSettings(listener)
  const changed = (event: StorageEvent) => {
    if (event.key === PROCESSING_PREFERENCES_KEY || event.key === null) listener()
  }
  window.addEventListener('storage', changed)
  return () => { listeners.delete(listener); window.removeEventListener('storage', changed); unsubscribeGeneral() }
}
export function useProcessingPreferences() {
  return [useSyncExternalStore(subscribe, getProcessingPreferences), updateProcessingPreferences] as const
}
