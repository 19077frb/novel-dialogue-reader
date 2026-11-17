import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { createRef } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApplicationSettings } from '../src/components/ApplicationSettings'
import type { ApplicationSettingsHandle } from '../src/components/ApplicationSettings'
import * as api from '../src/api/applicationSettings'
import { ApiError } from '../src/api/client'
import { renderWithProviders } from './helpers'
import * as restartApi from '../src/api/applicationRestart'

vi.mock('../src/api/applicationRestart', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/applicationRestart')>(),
  readRestartHealth: vi.fn(), waitForApplicationRestart: vi.fn(), openRestartedSettings: vi.fn(),
}))

vi.mock('../src/api/applicationSettings', () => ({
  fetchApplicationSettings: vi.fn(), saveApplicationSettings: vi.fn(), saveAndRestartApplication: vi.fn(),
  applicationSettingsKey: ['application-settings'],
}))
const example: api.ApplicationSettings = {
  revision: 'missing', config_path: 'test/application-settings.json', restart_required: [], restart_blocked_reason: null,
  fields: [
    { key: 'port', label: '服务端口', group: '服务与书库', description: '重启后使用新端口', kind: 'number', minimum: 1, maximum: 65535, options: [], value: 8765, current_value: 8765, default_value: 8765, locked_reason: null },
    { key: 'host', label: '监听地址', group: '服务与书库', description: '本地服务', kind: 'text', minimum: null, maximum: null, options: [], value: '127.0.0.1', current_value: '127.0.0.1', default_value: '127.0.0.1', locked_reason: '免安装版固定监听本机' },
  ],
}
function applicationSaveButton() {
  return within(screen.getByRole('region', { name: '应用配置' })).getByRole('button', { name: '保存设置' })
}
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(restartApi.readRestartHealth).mockResolvedValue({ started_at: 'old-instance', server_time: 'before-restart' } as never)
  vi.mocked(restartApi.waitForApplicationRestart).mockImplementation(() => new Promise(() => {}))
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(example)
})
afterEach(() => vi.restoreAllMocks())

const hiddenFields: api.ApplicationSettings['fields'] = ([
  ['credential_backend', '模型密钥保存方式', '凭据', 'session', 'system'],
  ['static_dir', '网页文件目录', '服务与书库', 'D:/reader/web', null],
  ['cors_origins', '允许访问接口的网页来源', '服务与书库', ['http://localhost:5173'], []],
  ['max_import_bytes', '导入文件大小上限（字节）', '导入与EPUB安全限制', 1048577, 0],
  ['max_epub_entries', 'EPUB内部文件数上限', '导入与EPUB安全限制', 2000, 0],
  ['max_epub_total_uncompressed_bytes', 'EPUB解压总大小上限（字节）', '导入与EPUB安全限制', 209715201, 0],
  ['max_epub_entry_bytes', 'EPUB单个内部文件大小上限（字节）', '导入与EPUB安全限制', 33554433, 0],
  ['max_epub_spine_items', 'EPUB正文文档数上限', '导入与EPUB安全限制', 1000, 0],
  ['allow_fake_provider', '启用离线测试提供方', '离线测试（高级）', true, false],
  ['fake_provider_labels', '离线测试标注方式', '离线测试（高级）', 'deterministic', 'unknown'],
  ['fake_provider_script', '离线测试错误脚本', '离线测试（高级）', 'timeout_once', ''],
] satisfies Array<[string, string, string, api.ApplicationSettingsPatch['values'][string], api.ApplicationSettingsPatch['values'][string]]>)
  .map(([key, label, group, value, defaultValue]) => ({
  ...example.fields[0], key, label, group, value, current_value: value, default_value: defaultValue,
  kind: Array.isArray(value) ? 'lines' : typeof value === 'boolean' ? 'boolean' : typeof value === 'string' ? 'text' : 'number',
}))

it('隐藏三组旧配置及网页配置并保持干净状态，后台待重启配置显示通用提示', async () => {
  const onStateChange = vi.fn()
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue({ ...example, fields: [...example.fields, ...hiddenFields],
    restart_required: ['credential_backend', 'max_import_bytes'] })
  renderWithProviders(<ApplicationSettings onStateChange={onStateChange} />)
  await screen.findByLabelText('服务端口')
  for (const field of hiddenFields) expect(screen.queryByLabelText(field.label)).not.toBeInTheDocument()
  for (const group of ['凭据', '导入与EPUB安全限制', '离线测试（高级）']) expect(screen.queryByText(group)).not.toBeInTheDocument()
  expect(applicationSaveButton()).toBeDisabled()
  expect(screen.queryByRole('button', { name: '重新读取应用配置' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '读取最新配置' })).not.toBeInTheDocument()
  expect(screen.getByText('已保存的应用配置将在重启后生效。')).toBeVisible()
  expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ changed: false, ready: true }))
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
})

it.each([false, true])('恢复仅修改可见未锁定配置，保存（重启：%s）保留全部隐藏旧值', async restart => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const settings = { ...example, fields: [
    { ...example.fields[0], value: 8800, current_value: 8800 },
    { ...example.fields[1], value: 'localhost', current_value: 'localhost' }, ...hiddenFields,
  ] }
  const hiddenBefore = structuredClone(hiddenFields)
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(settings)
  const save = restart ? api.saveAndRestartApplication : api.saveApplicationSettings
  vi.mocked(save).mockResolvedValue({ ...settings, fields: [{ ...settings.fields[0], value: 8765 }, ...settings.fields.slice(1)],
    restart_required: ['port', 'credential_backend'] })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(screen.getByLabelText('服务端口')).toHaveValue(8765)
  expect(screen.getByLabelText('监听地址')).toHaveValue('localhost')
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
  expect(api.saveAndRestartApplication).not.toHaveBeenCalled()
  await userEvent.click(restart ? screen.getByRole('button', { name: '保存并重启' }) : applicationSaveButton())
  await waitFor(() => expect(save).toHaveBeenCalledWith({ values: { port: 8765 }, revision: 'missing' }))
  expect(hiddenFields).toEqual(hiddenBefore)
  expect(screen.getByText(/已保存，重启后生效：服务端口/)).toHaveTextContent('其他应用配置也将在重启后生效。')
})

it('托管统一保存仅提交可见字段，隐藏旧值也不触发恢复后的修改状态', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const ref = createRef<ApplicationSettingsHandle>()
  const onStateChange = vi.fn()
  const settings = { ...example, fields: [...example.fields, ...hiddenFields] }
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(settings)
  vi.mocked(api.saveApplicationSettings).mockResolvedValue({ ...settings, fields: [{ ...example.fields[0], value: 8800 }, ...settings.fields.slice(1)] })
  renderWithProviders(<ApplicationSettings ref={ref} managed onStateChange={onStateChange} />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ changed: false }))
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  expect(onStateChange).toHaveBeenLastCalledWith(expect.objectContaining({ changed: true }))
  await act(() => ref.current!.save())
  expect(api.saveApplicationSettings).toHaveBeenCalledWith({ values: { port: 8800 }, revision: 'missing' })
})

it('编辑不自动保存，保存显示待重启状态和新端口', async () => {
  vi.mocked(api.saveApplicationSettings).mockResolvedValue({ ...example, revision: 'saved', restart_required: ['port'], fields: [{ ...example.fields[0], value: 8800 }, example.fields[1]] })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  expect(applicationSaveButton()).toBeVisible()
  expect(screen.getByLabelText('监听地址')).toBeDisabled()
  expect(screen.getByText(/免安装版固定监听本机/)).toBeVisible()
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
  await userEvent.click(applicationSaveButton())
  await waitFor(() => expect(api.saveApplicationSettings).toHaveBeenCalledWith({ values: { port: 8800 }, revision: 'missing' }))
  expect(await screen.findByText(/已保存，重启后生效/)).toBeVisible()
  expect(screen.getByText(/http:\/\/127.0.0.1:8800/)).toBeVisible()
})
it('保存并重启需要确认，服务拒绝时保留草稿和具体原因', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.saveAndRestartApplication).mockRejectedValue(new Error('仍有排队或运行中的任务'))
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('仍有排队或运行中的任务')
  expect(screen.getByLabelText('服务端口')).toHaveValue(8800)
})
it('不支持自动重启时按钮禁用并给出下一步；读取失败提供重试', async () => {
  vi.mocked(api.fetchApplicationSettings).mockRejectedValueOnce(new Error('连接失败'))
    .mockResolvedValue({ ...example, restart_blocked_reason: '此启动方式请手动重启服务' })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByText(/应用配置读取失败：连接失败/)
  await userEvent.click(screen.getByRole('button', { name: '重新读取应用配置' }))
  await screen.findByLabelText('服务端口')
  expect(screen.getByRole('button', { name: '保存并重启' })).toBeDisabled()
  expect(screen.getByText('此启动方式请手动重启服务')).toBeVisible()
})
it('版本冲突时提供读取最新配置，确认后丢弃草稿并使用最新版本重试', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
  const latest = { ...example, revision: 'latest', fields: [{ ...example.fields[0], value: 8790 }, example.fields[1], ...hiddenFields] }
  vi.mocked(api.fetchApplicationSettings).mockResolvedValueOnce(example).mockResolvedValue(latest)
  vi.mocked(api.saveApplicationSettings)
    .mockRejectedValueOnce(new ApiError(409, { code: 'VERSION_CONFLICT', message: '配置已在其他页面修改，请重新读取后再保存。' }))
    .mockResolvedValue({ ...latest, fields: [{ ...latest.fields[0], value: 8810 }, ...latest.fields.slice(1)] })
  renderWithProviders(<ApplicationSettings />)
  const port = await screen.findByLabelText('服务端口')
  fireEvent.change(port, { target: { value: '8800' } })
  await userEvent.click(applicationSaveButton())
  const reload = await screen.findByRole('button', { name: '读取最新配置' })
  await userEvent.click(reload)
  expect(confirm).toHaveBeenCalledWith('读取最新配置会放弃本页未保存的应用配置修改，是否继续？')
  expect(api.fetchApplicationSettings).toHaveBeenCalledTimes(1)
  expect(port).toHaveValue(8800)
  confirm.mockReturnValue(true)
  await userEvent.click(reload)
  await waitFor(() => expect(port).toHaveValue(8790))
  expect(api.fetchApplicationSettings).toHaveBeenCalledTimes(2)
  expect(applicationSaveButton()).toBeDisabled()
  expect(screen.queryByRole('button', { name: '读取最新配置' })).not.toBeInTheDocument()
  fireEvent.change(port, { target: { value: '8810' } })
  await userEvent.click(applicationSaveButton())
  await waitFor(() => expect(api.saveApplicationSettings).toHaveBeenNthCalledWith(2, { values: { port: 8810 }, revision: 'latest' }))
})
it('成功重启后锁定编辑并显示重新打开入口', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.saveAndRestartApplication).mockResolvedValue(example)
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  expect(await screen.findByRole('link', { name: '重新打开设置' })).toHaveAttribute('href', `${window.location.origin}/settings/general#application-settings`)
  expect(screen.getByRole('link', { name: '重新打开设置' })).toHaveClass('ndr-button')
  expect(screen.getByLabelText('服务端口')).toBeDisabled()
  expect(restartApi.readRestartHealth).toHaveBeenCalledTimes(1)
  expect(restartApi.waitForApplicationRestart).toHaveBeenCalledWith({ startedAt: 'old-instance', serverTime: 'before-restart' }, undefined, expect.any(AbortSignal))
})

it('当前服务状态读取失败时保留草稿，重启请求尚未发出', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(restartApi.readRestartHealth).mockRejectedValue(new Error('连接超时'))
  renderWithProviders(<ApplicationSettings />)
  fireEvent.change(await screen.findByLabelText('服务端口'), { target: { value: '8800' } })
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('设置尚未提交')
  expect(screen.getByLabelText('服务端口')).toHaveValue(8800)
  expect(screen.getByLabelText('服务端口')).toBeEnabled()
  expect(api.saveAndRestartApplication).not.toHaveBeenCalled()
  expect(restartApi.waitForApplicationRestart).not.toHaveBeenCalled()
})

it('恢复超时解除锁定，重新检测只读取健康状态并打开新地址', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.saveAndRestartApplication).mockResolvedValue({ ...example, fields: [
    { ...example.fields[0], value: 8800 }, example.fields[1],
  ] })
  vi.mocked(restartApi.waitForApplicationRestart).mockRejectedValueOnce(new Error('设置已保存，180 秒内尚未确认服务恢复。'))
    .mockResolvedValueOnce()
  renderWithProviders(<ApplicationSettings />)
  fireEvent.change(await screen.findByLabelText('服务端口'), { target: { value: '8800' } })
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('180 秒')
  expect(screen.getByLabelText('服务端口')).toBeEnabled()
  expect(screen.getByRole('button', { name: '保存并重启' })).toBeDisabled()
  expect(screen.getByRole('link', { name: '重新打开设置' })).toHaveAttribute('href', 'http://127.0.0.1:8800/settings/general#application-settings')
  await userEvent.click(screen.getByRole('button', { name: '重新检测服务' }))
  await waitFor(() => expect(restartApi.openRestartedSettings).toHaveBeenCalledWith('http://127.0.0.1:8800'))
  expect(restartApi.waitForApplicationRestart).toHaveBeenCalledTimes(2)
  expect(api.saveAndRestartApplication).toHaveBeenCalledTimes(1)
})

it('离开页面取消正在等待的恢复检测', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.saveAndRestartApplication).mockResolvedValue(example)
  const page = renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  await waitFor(() => expect(restartApi.waitForApplicationRestart).toHaveBeenCalledTimes(1))
  const signal = vi.mocked(restartApi.waitForApplicationRestart).mock.calls[0][2]
  expect(signal.aborted).toBe(false)
  page.unmount()
  expect(signal.aborted).toBe(true)
  expect(restartApi.openRestartedSettings).not.toHaveBeenCalled()
})

it('恢复应用配置只修改未锁定草稿，不自动保存或重启', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue({ ...example, fields: [
    { ...example.fields[0], value: 8800, current_value: 8800 },
    { ...example.fields[1], value: 'localhost', current_value: 'localhost' },
  ] })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(screen.getByLabelText('服务端口')).toHaveValue(8765)
  expect(screen.getByLabelText('监听地址')).toHaveValue('localhost')
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
  expect(api.saveAndRestartApplication).not.toHaveBeenCalled()
  expect(applicationSaveButton()).toBeEnabled()
})

it('取消应用配置恢复保留草稿，全锁定时提示无法恢复', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const page = renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(screen.getByLabelText('服务端口')).toHaveValue(8800)
  page.unmount()
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue({ ...example, fields: [
    ...example.fields.map(field => ({ ...field, locked_reason: '启动参数锁定' })), ...hiddenFields,
  ] })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  expect(screen.getByRole('button', { name: '恢复应用配置默认值' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '恢复应用配置默认值' })).toHaveAttribute('title', '当前页面没有可恢复的未锁定配置。')
})
