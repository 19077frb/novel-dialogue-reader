import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import SettingsPage from '../src/pages/SettingsPage'
import { getGeneralSettings, SETTINGS_KEY } from '../src/settings/preferences'
import * as profiles from '../src/api/profiles'
import * as applicationSettings from '../src/api/applicationSettings'
import { getProcessingPreferences } from '../src/processing/preferences'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/profiles', () => ({ fetchProfiles: vi.fn(), profileKeys: { profiles: () => ['profiles'] } }))
vi.mock('../src/api/applicationSettings', () => ({ fetchApplicationSettings: vi.fn(), saveApplicationSettings: vi.fn(), saveAndRestartApplication: vi.fn(), applicationSettingsKey: ['application-settings'] }))
beforeEach(() => {
  localStorage.clear(); vi.mocked(profiles.fetchProfiles).mockResolvedValue([])
  vi.mocked(applicationSettings.fetchApplicationSettings).mockResolvedValue({ fields: [], revision: 'missing', config_path: 'test', restart_required: [], restart_blocked_reason: '测试启动方式不重启' })
})
afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })
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
  await userEvent.click(screen.getByRole('button', { name: '恢复默认设置' }))
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
