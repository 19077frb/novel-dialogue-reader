import { beforeEach, expect, it, vi } from 'vitest'

beforeEach(() => { localStorage.clear(); vi.resetModules(); vi.restoreAllMocks() })

it.each([
  [{}, false, false, false],
  [{ enableExperimentalFeatures: true }, true, true, true],
  [{ enableExperimentalFeatures: false }, false, false, false],
  [{ enableExperimentalFeatures: 'true' }, false, false, false],
  [{ enableExperimentalFeatures: true, enableInitialReading: false }, true, false, true],
  [{ enableExperimentalFeatures: true, enableDialogueStrategies: false }, true, true, false],
  [{ enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: false }, true, true, false],
  [{ enableExperimentalFeatures: true, enableInitialReading: false, enableDialogueStrategies: true }, true, false, true],
  [{ enableExperimentalFeatures: true, enableInitialReading: 'true', enableDialogueStrategies: 'false' }, true, false, false],
  [{ enableExperimentalFeatures: true, enableInitialReading: 1, enableDialogueStrategies: null }, true, false, false],
  [{ enableExperimentalFeatures: false, enableInitialReading: true, enableDialogueStrategies: true }, false, true, true],
] as const)('normalizes experimental switches in %j without writing during reads', async (record, master, initial, dialogue) => {
  const settings = await import('../src/settings/preferences')
  const raw = JSON.stringify(record)
  localStorage.setItem(settings.SETTINGS_KEY, raw)
  const write = vi.spyOn(Storage.prototype, 'setItem')
  expect(settings.getGeneralSettings()).toMatchObject({
    enableExperimentalFeatures: master, enableInitialReading: initial, enableDialogueStrategies: dialogue,
  })
  expect(settings.getGeneralSettings()).toBe(settings.getGeneralSettings())
  expect(localStorage.getItem(settings.SETTINGS_KEY)).toBe(raw)
  expect(write).not.toHaveBeenCalled()
})

it('new preferences require individual choices and retain them when the master is disabled', async () => {
  const settings = await import('../src/settings/preferences')
  expect(settings.getGeneralSettings()).toMatchObject({
    enableExperimentalFeatures: false, enableInitialReading: false, enableDialogueStrategies: false,
  })
  settings.updateGeneralSettings({ enableExperimentalFeatures: true })
  expect(settings.getGeneralSettings()).toMatchObject({
    enableExperimentalFeatures: true, enableInitialReading: false, enableDialogueStrategies: false,
  })
  settings.updateGeneralSettings({ enableInitialReading: true, enableDialogueStrategies: true })
  settings.updateGeneralSettings({ enableExperimentalFeatures: false })
  vi.resetModules()
  const reopened = await import('../src/settings/preferences')
  expect(reopened.getGeneralSettings()).toMatchObject({
    enableExperimentalFeatures: false, enableInitialReading: true, enableDialogueStrategies: true,
  })
  reopened.updateGeneralSettings({ enableExperimentalFeatures: true })
  expect(reopened.getGeneralSettings()).toMatchObject({
    enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: true,
  })
})

it('materializes legacy switch choices on the next explicit save with the existing key', async () => {
  const settings = await import('../src/settings/preferences')
  localStorage.setItem(settings.SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: true, fontSize: 21 }))
  settings.updateGeneralSettings({ lineHeight: 2.1 })
  expect(JSON.parse(localStorage.getItem(settings.SETTINGS_KEY)!)).toMatchObject({
    enableExperimentalFeatures: true, enableInitialReading: true, enableDialogueStrategies: true,
    fontSize: 21, lineHeight: 2.1,
  })
  expect(Object.keys(localStorage)).toEqual(['ndr:general-settings:v1'])
})

it('falls back to disabled experimental switches for malformed records', async () => {
  const settings = await import('../src/settings/preferences')
  for (const raw of ['{bad', 'null', '"invalid"']) {
    localStorage.setItem(settings.SETTINGS_KEY, raw)
    expect(settings.getGeneralSettings()).toMatchObject({
      enableExperimentalFeatures: false, enableInitialReading: false, enableDialogueStrategies: false,
    })
  }
})
