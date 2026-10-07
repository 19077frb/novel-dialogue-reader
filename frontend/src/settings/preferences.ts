import { useSyncExternalStore } from 'react'
import { defaultChapterFilter, normalizeChapterFilter } from '../processing/chapterFilter'

export const SETTINGS_KEY = 'ndr:general-settings:v1'
export const defaultSettings = { ...defaultChapterFilter(), fontSize: 16, lineHeight: 1.95, resumeReading: true, showCandidates: true, showAnnotations: true, autoProcessing: false, lookAheadChapters: 2, doubleClickChapterStatus: false, allowOverwriteManualCharacters: false }
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
      cache = { ...normalizeChapterFilter(value), fontSize: Number.isFinite(value.fontSize) ? Math.min(28, Math.max(14, value.fontSize)) : 16,
        lineHeight: Number.isFinite(value.lineHeight) ? Math.min(2.6, Math.max(1.5, value.lineHeight)) : 1.95,
        resumeReading: typeof value.resumeReading === 'boolean' ? value.resumeReading : true,
        showCandidates: typeof value.showCandidates === 'boolean' ? value.showCandidates : true,
        showAnnotations: typeof value.showAnnotations === 'boolean' ? value.showAnnotations : true,
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
function subscribe(listener: () => void) {
  listeners.add(listener)
  const changed = (event: StorageEvent) => { if (event.key === SETTINGS_KEY || event.key === null) listener() }
  window.addEventListener('storage', changed)
  return () => { listeners.delete(listener); window.removeEventListener('storage', changed) }
}
export function useGeneralSettings() {
  return [useSyncExternalStore(subscribe, getGeneralSettings), updateGeneralSettings] as const
}
