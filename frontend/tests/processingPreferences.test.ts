import { beforeEach, describe, expect, it, vi } from 'vitest'

describe('shared processing preferences', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); vi.restoreAllMocks() })

  it('persists one configuration across module reloads without credentials', async () => {
    const first = await import('../src/processing/preferences')
    first.updateProcessingPreferences({ profileId: 'chosen', concurrency: 5, maxRechecks: 8, tokenLimit: 12345, maxOutputTokens: 200 })
    vi.resetModules()
    const rebooted = await import('../src/processing/preferences')
    expect(rebooted.getProcessingPreferences()).toEqual({
      profileId: 'chosen', concurrency: 5, maxRechecks: 8, tokenLimit: 12345, maxOutputTokens: 200,
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
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, JSON.stringify({ concurrency: 100, tokenLimit: -4, maxRechecks: 1.5, profileId: 42 }))
    expect(module.getProcessingPreferences()).toEqual({ profileId: '', concurrency: 16, tokenLimit: null, maxOutputTokens: null, maxRechecks: 0 })
    localStorage.setItem(module.PROCESSING_PREFERENCES_KEY, 'null')
    expect(module.getProcessingPreferences().tokenLimit).toBeNull()
  })

  it('keeps controls usable in memory when browser storage is unavailable', async () => {
    const module = await import('../src/processing/preferences')
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    module.updateProcessingPreferences({ concurrency: 4, maxRechecks: 3 })
    expect(module.getProcessingPreferences().concurrency).toBe(4)
    expect(module.getProcessingPreferences().maxRechecks).toBe(3)
  })
})
