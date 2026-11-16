import { act, renderHook } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'
import { SETTINGS_KEY, updateGeneralSettings } from '../src/settings/preferences'
import {
  PROCESSING_PREFERENCES_KEY, getStoredProcessingPreferences, updateProcessingPreferences,
  useProcessingPreferences, useStoredProcessingPreferences,
} from '../src/processing/preferences'

beforeEach(() => localStorage.clear())

it('exposes raw choices for editing while effective preferences follow each saved gate', () => {
  updateProcessingPreferences({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  const { result } = renderHook(() => ({ raw: useStoredProcessingPreferences(), effective: useProcessingPreferences()[0] }))
  const original = result.current.raw[0]
  const update = result.current.raw[1]
  expect(original).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  expect(result.current.effective).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false })
  act(() => updateGeneralSettings({ enableExperimentalFeatures: true }))
  expect(result.current.raw[0]).toBe(original)
  expect(result.current.raw[1]).toBe(update)
  expect(result.current.effective).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: true })
  act(() => updateGeneralSettings({ enableDialogueStrategies: true }))
  expect(result.current.effective.dialogueStrategy).toBe('complete-review')
  act(() => updateGeneralSettings({ enableExperimentalFeatures: false }))
  act(() => result.current.raw[1]({ concurrency: 4 }))
  expect(result.current.raw[0]).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true, concurrency: 4 })
  expect(result.current.effective).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false, concurrency: 4 })
  expect(result.current.raw[0]).toBe(getStoredProcessingPreferences())
})

it('receives cross-tab raw preference and independent gate changes', () => {
  updateGeneralSettings({ enableExperimentalFeatures: true })
  const { result } = renderHook(() => ({ raw: useStoredProcessingPreferences()[0], effective: useProcessingPreferences()[0] }))
  act(() => {
    localStorage.setItem(PROCESSING_PREFERENCES_KEY, JSON.stringify({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true }))
    window.dispatchEvent(new StorageEvent('storage', { key: PROCESSING_PREFERENCES_KEY }))
  })
  expect(result.current.raw).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  expect(result.current.effective).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: true })
  act(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: true, enableDialogueStrategies: true, enableInitialReading: false }))
    window.dispatchEvent(new StorageEvent('storage', { key: SETTINGS_KEY }))
  })
  expect(result.current.effective.dialogueStrategy).toBe('complete-review')
  act(() => {
    localStorage.removeItem(SETTINGS_KEY)
    window.dispatchEvent(new StorageEvent('storage', { key: SETTINGS_KEY }))
  })
  expect(result.current.effective).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false })
  expect(result.current.raw).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
})
