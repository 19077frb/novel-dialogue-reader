import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import SettingsPage from '../src/pages/SettingsPage'
import { getGeneralSettings, updateGeneralSettings, SETTINGS_KEY } from '../src/settings/preferences'
import * as profiles from '../src/api/profiles'
import * as applicationSettings from '../src/api/applicationSettings'
import { getProcessingPreferences, updateProcessingPreferences } from '../src/processing/preferences'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/profiles', () => ({ fetchProfiles: vi.fn(), profileKeys: { profiles: () => ['profiles'] } }))
vi.mock('../src/api/applicationSettings', () => ({ fetchApplicationSettings: vi.fn(), saveApplicationSettings: vi.fn(), saveAndRestartApplication: vi.fn(), applicationSettingsKey: ['application-settings'] }))
beforeEach(() => {
  localStorage.clear(); vi.mocked(profiles.fetchProfiles).mockResolvedValue([])
  vi.mocked(applicationSettings.fetchApplicationSettings).mockResolvedValue({ fields: [], revision: 'missing', config_path: 'test', restart_required: [], restart_blocked_reason: '测试启动方式不重启' })
})
afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })
it('keeps the dialogue strategy as a draft until save and restores it on reopening', async () => {
  const page = renderWithProviders(<SettingsPage />)
  await userEvent.selectOptions(screen.getByTestId('dialogue-strategy'), 'complete-review')
  expect(getProcessingPreferences().dialogueStrategy).toBe('legacy')
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getProcessingPreferences().dialogueStrategy).toBe('complete-review')
  page.unmount()
  renderWithProviders(<SettingsPage />)
  expect(screen.getByTestId('dialogue-strategy')).toHaveValue('complete-review')
  expect(screen.getByText(/当前复核次数为 0/)).toBeInTheDocument()
})
it('后台更新人工人物默认关闭，开启后重新进入仍保留', async () => {
  const page = renderWithProviders(<SettingsPage />)
  const label = '允许后台人物识别更新人工姓名与说明'
  expect(screen.getByLabelText(label)).not.toBeChecked()
  await userEvent.click(screen.getByLabelText(label))
  expect(getGeneralSettings().allowOverwriteManualCharacters).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings().allowOverwriteManualCharacters).toBe(true)
  page.unmount()
  renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText(label)).toBeChecked()
})
it('persists display preferences and rehydrates the same values on reopening', async () => {
  const first = renderWithProviders(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('正文字号'), { target: { value: 22 } })
  fireEvent.change(screen.getByLabelText('正文行距'), { target: { value: 2.2 } })
  await userEvent.click(screen.getByLabelText(/默认显示候选引语/))
  expect(localStorage.getItem(SETTINGS_KEY)).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!)).toMatchObject({ fontSize: 22, lineHeight: 2.2, showCandidates: false })
  first.unmount()
  renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText('正文字号')).toHaveValue(22)
  expect(screen.getByLabelText(/默认显示候选引语/)).not.toBeChecked()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  await userEvent.click(screen.getByRole('button', { name: '恢复阅读显示默认值' }))
  expect(screen.getByLabelText('正文字号')).toHaveValue(16)
  expect(getGeneralSettings().fontSize).toBe(22)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings().fontSize).toBe(16)
})
it('bounds corrupt stored values and falls back safely for invalid JSON', () => {
  localStorage.setItem(SETTINGS_KEY, '{bad')
  expect(getGeneralSettings().fontSize).toBe(16)
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ fontSize: 1000, lineHeight: -1, showAnnotations: 'no' }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 28, lineHeight: 1.5, showAnnotations: true })
})

it('双击目录状态默认关闭，开启后保存并在重新进入设置时恢复', async () => {
  const page = renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText('双击目录章节名切换完成状态')).not.toBeChecked()
  await userEvent.click(screen.getByLabelText('双击目录章节名切换完成状态'))
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings().doubleClickChapterStatus).toBe(true)
  page.unmount()
  renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText('双击目录章节名切换完成状态')).toBeChecked()
})

it('keeps automatic processing off by default and remembers look-ahead and shared limits', async () => {
  renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText(/阅读时自动处理当前章/)).not.toBeChecked()
  await userEvent.click(screen.getByLabelText(/阅读时自动处理当前章/))
  fireEvent.change(screen.getByLabelText('提前处理后续章节数'), { target: { value: '4' } })
  fireEvent.change(screen.getByLabelText('最大并发任务数'), { target: { value: '3' } })
  fireEvent.change(screen.getByLabelText('自动处理 Token 上限（留空＝不限）'), { target: { value: '50000' } })
  expect(getGeneralSettings().autoProcessing).toBe(false)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings()).toMatchObject({ autoProcessing: true, lookAheadChapters: 4 })
  expect(getProcessingPreferences()).toMatchObject({ concurrency: 3, tokenLimit: 50000 })
})

it('恢复阅读显示不改变人物及自动处理区域，保存前不写浏览器设置', async () => {
  updateGeneralSettings({ fontSize: 22, allowOverwriteManualCharacters: true, autoProcessing: true, lookAheadChapters: 8 })
  updateProcessingPreferences({ concurrency: 5, tokenLimit: 4000, thinkingMode: 'enabled' })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderWithProviders(<SettingsPage />)
  await userEvent.click(screen.getByRole('button', { name: '恢复阅读显示默认值' }))
  expect(screen.getByLabelText('正文字号')).toHaveValue(16)
  expect(screen.getByLabelText('允许后台人物识别更新人工姓名与说明')).toBeChecked()
  expect(screen.getByLabelText(/阅读时自动处理当前章/)).toBeChecked()
  expect(getGeneralSettings().fontSize).toBe(22)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 16, allowOverwriteManualCharacters: true, autoProcessing: true, lookAheadChapters: 8 })
  expect(getProcessingPreferences()).toMatchObject({ concurrency: 5, tokenLimit: 4000, thinkingMode: 'enabled' })
})

it('恢复人物资料更新仅关闭该开关，保留阅读和自动处理设置', async () => {
  updateGeneralSettings({ fontSize: 22, allowOverwriteManualCharacters: true, autoProcessing: true, lookAheadChapters: 8 })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderWithProviders(<SettingsPage />)
  await userEvent.click(screen.getByRole('button', { name: '恢复人物资料更新默认值' }))
  expect(getGeneralSettings().allowOverwriteManualCharacters).toBe(true)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 22, allowOverwriteManualCharacters: false, autoProcessing: true, lookAheadChapters: 8 })
})

it('恢复自动处理涵盖本栏可见参数，但保留其他区域及隐藏处理参数', async () => {
  updateGeneralSettings({ fontSize: 22, allowOverwriteManualCharacters: true, autoProcessing: true, lookAheadChapters: 8 })
  updateProcessingPreferences({ profileId: 'existing-profile', concurrency: 5, tokenLimit: 4000, maxRecheckRounds: 3,
    thinkingMode: 'enabled', thinkingEffort: 'high', maxFormatRetries: 5, maxOutputTokens: 8000 })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  renderWithProviders(<SettingsPage />)
  await userEvent.click(screen.getByRole('button', { name: '恢复自动处理默认值' }))
  expect(screen.getByLabelText(/阅读时自动处理当前章/)).not.toBeChecked()
  expect(screen.getByLabelText('最大并发任务数')).toHaveValue(2)
  expect(screen.getByTestId('processing-thinking-mode')).toHaveValue('default')
  expect(getProcessingPreferences().concurrency).toBe(5)
  expect(getGeneralSettings().autoProcessing).toBe(true)
  await userEvent.click(screen.getByRole('button', { name: '保存阅读与处理设置' }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 22, allowOverwriteManualCharacters: true, autoProcessing: false, lookAheadChapters: 2 })
  expect(getProcessingPreferences()).toMatchObject({ profileId: '', concurrency: 2, tokenLimit: null, maxRecheckRounds: 0,
    thinkingMode: 'default', thinkingEffort: 'default', maxFormatRetries: 5, maxOutputTokens: 8000 })
})

it('取消任意区域恢复时不改变草稿或已保存配置', async () => {
  updateGeneralSettings({ fontSize: 22, allowOverwriteManualCharacters: true, autoProcessing: true })
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  renderWithProviders(<SettingsPage />)
  for (const name of ['恢复阅读显示默认值', '恢复人物资料更新默认值', '恢复自动处理默认值']) {
    await userEvent.click(screen.getByRole('button', { name }))
  }
  expect(screen.getByLabelText('正文字号')).toHaveValue(22)
  expect(screen.getByLabelText('允许后台人物识别更新人工姓名与说明')).toBeChecked()
  expect(screen.getByLabelText(/阅读时自动处理当前章/)).toBeChecked()
  expect(screen.getByRole('button', { name: '保存阅读与处理设置' })).toBeDisabled()
})
