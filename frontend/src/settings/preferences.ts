import { useSyncExternalStore } from 'react'
import { defaultChapterFilter, normalizeChapterFilter } from '../processing/chapterFilter'

export const SETTINGS_KEY = 'ndr:general-settings:v1'
export const defaultSettings = { ...defaultChapterFilter(), enableExperimentalFeatures: false, enableInitialReading: false, enableDialogueStrategies: false, fontSize: 16, lineHeight: 1.95, resumeReading: true, showCandidates: true, showAnnotations: true, showReviewMarkers: false, autoProcessing: false, lookAheadChapters: 2, doubleClickChapterStatus: false, allowOverwriteManualCharacters: false }
export type GeneralSettings = typeof defaultSettings
let fallback = JSON.stringify(defaultSettings)
let rawCache: string | undefined
let cache = defaultSettings
const listeners = new Set<() => void>()
export function getGeneralSettings(): GeneralSettings {
  let raw = fallback
  try { raw = localStorage.getItem(SETTINGS_KEY) ?? JSON.stringify(defaultSettings) } catch { /* 会话内回退 */ }
  if (raw !== rawCache) {
    rawCache = raw
    try {
      const value = JSON.parse(raw)
      const experimental = value.enableExperimentalFeatures === true
      cache = { ...normalizeChapterFilter(value), fontSize: Number.isFinite(value.fontSize) ? Math.min(28, Math.max(14, value.fontSize)) : 16,
        lineHeight: Number.isFinite(value.lineHeight) ? Math.min(2.6, Math.max(1.5, value.lineHeight)) : 1.95,
        resumeReading: typeof value.resumeReading === 'boolean' ? value.resumeReading : true,
        showCandidates: typeof value.showCandidates === 'boolean' ? value.showCandidates : true,
        showAnnotations: typeof value.showAnnotations === 'boolean' ? value.showAnnotations : true,
        showReviewMarkers: value.showReviewMarkers === true,
        enableExperimentalFeatures: experimental,
        enableInitialReading: value.enableInitialReading === true || (experimental && !Object.hasOwn(value, 'enableInitialReading')),
        enableDialogueStrategies: value.enableDialogueStrategies === true || (experimental && !Object.hasOwn(value, 'enableDialogueStrategies')),
        autoProcessing: typeof value.autoProcessing === 'boolean' ? value.autoProcessing : false,
        allowOverwriteManualCharacters: value.allowOverwriteManualCharacters === true,
        doubleClickChapterStatus: typeof value.doubleClickChapterStatus === 'boolean' ? value.doubleClickChapterStatus : false,
        lookAheadChapters: Number.isSafeInteger(value.lookAheadChapters) ? Math.min(100, Math.max(0, value.lookAheadChapters)) : 2 }
    } catch { cache = defaultSettings }
  }
  return cache
}
export function updateGeneralSettings(patch: Partial<GeneralSettings>) {
  fallback = JSON.stringify({ ...getGeneralSettings(), ...patch })
  try { localStorage.setItem(SETTINGS_KEY, fallback) } catch { /* 会话内仍可使用 */ }
  rawCache = undefined
  listeners.forEach(listener => listener())
}
export function generalSettingsPersisted(expected: GeneralSettings): boolean {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) ?? 'null')
    return stored !== null && Object.entries(expected).every(([key, value]) => JSON.stringify(stored[key]) === JSON.stringify(value))
  } catch { return false }
}
export function subscribeGeneralSettings(listener: () => void) {
  listeners.add(listener)
  const changed = (event: StorageEvent) => { if (event.key === SETTINGS_KEY || event.key === null) listener() }
  window.addEventListener('storage', changed)
  return () => { listeners.delete(listener); window.removeEventListener('storage', changed) }
}
export function useGeneralSettings() {
  return [useSyncExternalStore(subscribeGeneralSettings, getGeneralSettings), updateGeneralSettings] as const
}
