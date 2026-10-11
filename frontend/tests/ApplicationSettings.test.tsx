import { fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ApplicationSettings } from '../src/components/ApplicationSettings'
import * as api from '../src/api/applicationSettings'
import { renderWithProviders } from './helpers'

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
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(example)
})
afterEach(() => vi.restoreAllMocks())

const capacityExamples = [
  ['max_import_bytes', '导入文件大小上限', 50],
  ['max_epub_total_uncompressed_bytes', 'EPUB解压总大小上限', 200],
  ['max_epub_entry_bytes', 'EPUB单个内部文件大小上限', 32],
] as const

it.each(capacityExamples)('%s 使用MB展示、范围和小数输入，保存仍使用整数字节', async (key, label, amount) => {
  const field = { ...example.fields[0], key, label: `${label}（字节）`, value: amount * 1024 ** 2,
    current_value: amount * 1024 ** 2, default_value: amount * 1024 ** 2, minimum: 1, maximum: 10 * 1024 ** 3 }
  const settings = { ...example, fields: [...example.fields, field] }
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(settings)
  vi.mocked(api.saveApplicationSettings).mockResolvedValue({ ...settings, restart_required: [key],
    fields: [...example.fields, { ...field, value: 100.5 * 1024 ** 2 }] })
  renderWithProviders(<ApplicationSettings />)
  const input = await screen.findByLabelText(`${label}（MB）`)
  expect(input).toHaveValue(amount)
  expect(input).toHaveAttribute('min', String(1 / 1024 ** 2))
  expect(input).toHaveAttribute('max', '10240')
  expect(input).toHaveAttribute('step', 'any')
  fireEvent.change(input, { target: { value: '100.5' } })
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '保存应用配置' }))
  await waitFor(() => expect(api.saveApplicationSettings).toHaveBeenCalledWith({ values: { [key]: 100.5 * 1024 ** 2 }, revision: 'missing' }))
  expect(await screen.findByText(`当前生效：${amount} MB`)).toBeVisible()
  expect(input).toHaveValue(100.5)
  expect(screen.getByText(/已保存，重启后生效/)).toHaveTextContent(`${label}（MB）`)
})

it('字节旧配置精确显示，恢复容量默认及保存并重启不会二次换算', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  const field = { ...example.fields[0], key: 'max_import_bytes', label: '导入文件大小上限（字节）',
    value: 1048577, current_value: 1048577, default_value: 50 * 1024 ** 2 }
  const settings = { ...example, fields: [...example.fields, field] }
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue(settings)
  vi.mocked(api.saveAndRestartApplication).mockResolvedValue(settings)
  renderWithProviders(<ApplicationSettings />)
  const input = await screen.findByLabelText('导入文件大小上限（MB）')
  expect(input).toHaveValue(1048577 / 1024 ** 2)
  expect(screen.getByRole('button', { name: '保存应用配置' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(input).toHaveValue(50)
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  await waitFor(() => expect(api.saveAndRestartApplication).toHaveBeenCalledWith({ values: { max_import_bytes: 50 * 1024 ** 2 }, revision: 'missing' }))
})

it('编辑不自动保存，保存显示待重启状态和新端口', async () => {
  vi.mocked(api.saveApplicationSettings).mockResolvedValue({ ...example, revision: 'saved', restart_required: ['port'], fields: [{ ...example.fields[0], value: 8800 }, example.fields[1]] })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  expect(screen.getByLabelText('监听地址')).toBeDisabled()
  expect(screen.getByText(/免安装版固定监听本机/)).toBeVisible()
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  expect(api.saveApplicationSettings).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '保存应用配置' }))
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
it('成功重启后锁定编辑并显示重新打开入口', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  vi.mocked(api.saveAndRestartApplication).mockResolvedValue(example)
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  await userEvent.click(screen.getByRole('button', { name: '保存并重启' }))
  expect(await screen.findByRole('link', { name: '重新打开设置' })).toHaveAttribute('href', 'http://127.0.0.1:8765/settings/general')
  expect(screen.getByRole('link', { name: '重新打开设置' })).toHaveClass('ndr-button')
  expect(screen.getByLabelText('服务端口')).toBeDisabled()
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
  expect(screen.getByRole('button', { name: '保存应用配置' })).toBeEnabled()
})

it('取消应用配置恢复保留草稿，全锁定时提示无法恢复', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  const page = renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  fireEvent.change(screen.getByLabelText('服务端口'), { target: { value: '8800' } })
  await userEvent.click(screen.getByRole('button', { name: '恢复应用配置默认值' }))
  expect(screen.getByLabelText('服务端口')).toHaveValue(8800)
  page.unmount()
  vi.mocked(api.fetchApplicationSettings).mockResolvedValue({ ...example, fields: example.fields.map(field => ({ ...field, locked_reason: '启动参数锁定' })) })
  renderWithProviders(<ApplicationSettings />)
  await screen.findByLabelText('服务端口')
  expect(screen.getByRole('button', { name: '恢复应用配置默认值' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '恢复应用配置默认值' })).toHaveAttribute('title', expect.stringContaining('全部应用配置'))
})
