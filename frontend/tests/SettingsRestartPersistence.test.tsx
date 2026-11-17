import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import SettingsPage from '../src/pages/SettingsPage'
import * as application from '../src/api/applicationSettings'
import * as restart from '../src/api/applicationRestart'
import { getStoredProcessingPreferences, PROCESSING_PREFERENCES_KEY } from '../src/processing/preferences'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/profiles', () => ({ fetchProfiles: vi.fn().mockResolvedValue([]), profileKeys: { profiles: () => ['profiles'] } }))
vi.mock('../src/api/applicationSettings', () => ({
  fetchApplicationSettings: vi.fn(), saveApplicationSettings: vi.fn(), saveAndRestartApplication: vi.fn(),
  applicationSettingsKey: ['application-settings'],
}))
vi.mock('../src/api/applicationRestart', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/applicationRestart')>(),
  readRestartHealth: vi.fn(), waitForApplicationRestart: vi.fn(), openRestartedSettings: vi.fn(),
}))
afterEach(() => { vi.restoreAllMocks(); localStorage.clear() })

it('仅处理偏好写入失败后仍可直接保存，再次检测使用新实例且重启只提交一次', async () => {
  localStorage.clear()
  const settings = { fields: [], revision: 'original', config_path: 'test', restart_required: [], restart_blocked_reason: null }
  vi.mocked(application.fetchApplicationSettings).mockResolvedValue(settings)
  vi.mocked(application.saveAndRestartApplication).mockResolvedValue(settings)
  vi.mocked(restart.readRestartHealth).mockResolvedValue({ started_at: 'old', server_time: 'before' } as never)
  vi.mocked(restart.waitForApplicationRestart).mockResolvedValue()
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  let storageFailed = true
  const originalSetItem = Storage.prototype.setItem
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(function (this: Storage, key, value) {
    if (key === PROCESSING_PREFERENCES_KEY && storageFailed) throw new DOMException('存储已满', 'QuotaExceededError')
    return originalSetItem.call(this, key, value)
  })
  renderWithProviders(<SettingsPage />, '/settings/general#automatic-settings')
  fireEvent.change(screen.getByTestId('processing-thinking-mode'), { target: { value: 'enabled' } })
  await userEvent.click(screen.getByRole('link', { name: '应用配置' }))
  const restartButton = screen.getByRole('button', { name: '保存并重启' })
  await waitFor(() => expect(restartButton).toBeEnabled())
  await userEvent.click(restartButton)
  expect(await screen.findByRole('alert')).toHaveTextContent('自动重新打开已暂停')
  expect(getStoredProcessingPreferences().thinkingMode).toBe('enabled')
  expect(localStorage.getItem(PROCESSING_PREFERENCES_KEY)).toBeNull()
  const save = screen.getByRole('button', { name: '保存设置' })
  expect(save).toBeEnabled()
  await userEvent.click(screen.getByRole('button', { name: '重新检测服务' }))
  expect(confirm).toHaveBeenCalledTimes(1)
  expect(restart.openRestartedSettings).not.toHaveBeenCalled()

  storageFailed = false
  await userEvent.click(save)
  await waitFor(() => expect(save).toBeDisabled())
  expect(JSON.parse(localStorage.getItem(PROCESSING_PREFERENCES_KEY)!).thinkingMode).toBe('enabled')
  expect(application.saveApplicationSettings).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '重新检测服务' }))
  await waitFor(() => expect(restart.openRestartedSettings).toHaveBeenCalledTimes(1))
  expect(application.saveAndRestartApplication).toHaveBeenCalledTimes(1)
  expect(confirm).toHaveBeenCalledTimes(1)
})
