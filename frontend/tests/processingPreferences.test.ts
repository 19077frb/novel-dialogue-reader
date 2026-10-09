import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('shared processing preferences', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); vi.restoreAllMocks() })

  it('persists one configuration across module reloads without credentials', async () => {
    const first = await import('../src/processing/preferences')
    first.updateProcessingPreferences({ profileId: 'chosen', concurrency: 5, maxRecheckRounds: 8, maxFormatRetries: 3, tokenLimit: 12345, maxOutputTokens: 200 })
    vi.resetModules()
    const rebooted = await import('../src/processing/preferences')
    expect(rebooted.getProcessingPreferences()).toEqual({
      profileId: 'chosen', concurrency: 5, maxRecheckRounds: 8, maxFormatRetries: 3, tokenLimit: 12345, maxOutputTokens: 200,
      thinkingMode: 'default', thinkingEffort: 'default',
      dialogueStrategy: 'legacy',
      rosterRepairEnabled: false, maxRosterRepairs: 1,
    })
    rebooted.updateProcessingPreferences({ tokenLimit: null })
    expect(rebooted.getProcessingPreferences().profileId).toBe('chosen')
    expect(rebooted.getProcessingPreferences().tokenLimit).toBeNull()
    expect(Object.keys(localStorage)).toEqual([first.PROCESSING_PREFERENCES_KEY])
  })

  it('handles malformed, partial and invalid stored values safely', async () => {
    const module = await import('../src/processing/preferences')
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, '{bad')
    expect(module.getProcessingPreferences().concurrency).toBe(2)
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, JSON.stringify({ concurrency: 100, tokenLimit: -4, maxRecheckRounds: 1.5, profileId: 42 }))
    expect(module.getProcessingPreferences()).toEqual({ profileId: '', concurrency: 16, tokenLimit: null, maxOutputTokens: null, maxRecheckRounds: 0, maxFormatRetries: 1, thinkingMode: 'default', thinkingEffort: 'default', dialogueStrategy: 'legacy', rosterRepairEnabled: false, maxRosterRepairs: 1 })
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, 'null')
    expect(module.getProcessingPreferences().tokenLimit).toBeNull()
  })

  it('does not turn legacy quote counts into paid full-window rounds', async () => {
    const module = await import('../src/processing/preferences')
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, JSON.stringify({
      profileId: 'chosen', concurrency: 5, maxRechecks: 20, tokenLimit: 12000,
    }))
    expect(module.getProcessingPreferences()).toMatchObject({
      profileId: 'chosen', concurrency: 5, maxRecheckRounds: 0, tokenLimit: 12000,
    })
  })

  it('keeps controls usable in memory when browser storage is unavailable', async () => {
    const module = await import('../src/processing/preferences')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    module.updateProcessingPreferences({ concurrency: 4, maxRecheckRounds: 3 })
    expect(module.getProcessingPreferences().concurrency).toBe(4)
    expect(module.getProcessingPreferences().maxRecheckRounds).toBe(3)
  })

  it.each(['complete-review', 'complete-blocks', 'complete-blocks-review', 'complete-blocks-isolated', 'complete-blocks-isolated-review', 'complete-blocks-isolated-feedback-review'] as const)('persists %s and never upgrades old or unknown values', async dialogueStrategy => {
    ;(await import('../src/settings/preferences')).updateGeneralSettings({ enableExperimentalFeatures: true })
    const module = await import('../src/processing/preferences')
    expect(module.getProcessingPreferences().dialogueStrategy).toBe('legacy')
    module.updateProcessingPreferences({ dialogueStrategy })
    vi.resetModules()
    expect((await import('../src/processing/preferences')).getProcessingPreferences().dialogueStrategy).toBe(dialogueStrategy)
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, JSON.stringify({ dialogueStrategy: 'new-future-mode' }))
    expect(module.getProcessingPreferences().dialogueStrategy).toBe('legacy')
  })

  it('persists explicit repair settings and safely normalizes old or invalid values', async () => {
    ;(await import('../src/settings/preferences')).updateGeneralSettings({ enableExperimentalFeatures: true })
    const module = await import('../src/processing/preferences')
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, JSON.stringify({ rosterRepairEnabled: 'true', maxRosterRepairs: -1 }))
    expect(module.getProcessingPreferences()).toMatchObject({ rosterRepairEnabled: false, maxRosterRepairs: 1 })
    module.updateProcessingPreferences({ rosterRepairEnabled: true, maxRosterRepairs: 9 })
    vi.resetModules()
    expect((await import('../src/processing/preferences')).getProcessingPreferences()).toMatchObject({ rosterRepairEnabled: true, maxRosterRepairs: 5 })
  })

  it('hides saved experiments from new workflows without rewriting frozen tasks or saved choices', async () => {
    const general = await import('../src/settings/preferences')
    const module = await import('../src/processing/preferences')
    expect(general.getGeneralSettings().enableExperimentalFeatures).toBe(false)
    module.updateProcessingPreferences({ dialogueStrategy: 'complete-blocks-isolated-review', rosterRepairEnabled: true })
    const frozen = { ...module.getStoredProcessingPreferences() }
    expect(module.getProcessingPreferences()).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false })
    expect(module.getProcessingPreferences()).toBe(module.getProcessingPreferences())
    module.updateProcessingPreferences({ concurrency: 4 })
    expect(module.getStoredProcessingPreferences()).toMatchObject({ dialogueStrategy: frozen.dialogueStrategy, rosterRepairEnabled: true })
    general.updateGeneralSettings({ enableExperimentalFeatures: true })
    expect(module.getProcessingPreferences()).toMatchObject({ dialogueStrategy: frozen.dialogueStrategy, rosterRepairEnabled: true, concurrency: 4 })
    general.updateGeneralSettings({ enableExperimentalFeatures: false })
    expect(module.getProcessingPreferences().dialogueStrategy).toBe('legacy')
    expect(frozen.rosterRepairEnabled).toBe(true)
    expect(module.rosterRepairOptions(frozen).rosterRepairEnabled).toBe(true)
  })
})
