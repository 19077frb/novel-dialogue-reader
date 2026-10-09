import { act, renderHook, render, screen } from '@testing-library/react'
import { beforeEach, expect, it } from 'vitest'
import { getGeneralSettings, updateGeneralSettings, SETTINGS_KEY } from '../src/settings/preferences'
import { getStoredProcessingPreferences, updateProcessingPreferences, useProcessingPreferences } from '../src/processing/preferences'
import { DialogueStrategySettings } from '../src/components/DialogueStrategySettings'
import { RosterRepairSettings } from '../src/components/RosterRepairSettings'

beforeEach(() => localStorage.clear())

it('requires a real boolean opt-in and keeps experimental controls absent by default', () => {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: 'true' }))
  expect(getGeneralSettings().enableExperimentalFeatures).toBe(false)
  render(<><DialogueStrategySettings value="legacy" onChange={() => {}} rounds={1} />
    <RosterRepairSettings preferences={getStoredProcessingPreferences()} onChange={() => {}} /></>)
  expect(screen.queryByTestId('dialogue-strategy')).not.toBeInTheDocument()
  expect(screen.queryByTestId('roster-repair-enabled')).not.toBeInTheDocument()
})

it('reacts to saved and cross-tab general settings without losing remembered experimental choices', () => {
  updateProcessingPreferences({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  const { result } = renderHook(() => useProcessingPreferences()[0])
  expect(result.current.dialogueStrategy).toBe('legacy')
  act(() => updateGeneralSettings({ enableExperimentalFeatures: true }))
  expect(result.current).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  act(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: false }))
    window.dispatchEvent(new StorageEvent('storage', { key: SETTINGS_KEY }))
  })
  expect(result.current).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false })
  expect(getStoredProcessingPreferences().rosterRepairEnabled).toBe(true)
})
