import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/client'
import * as profilesApi from '../src/api/profiles'
import type { ConnectionTestOut, ModelProfileOut, ProtocolCapabilitiesOut } from '../src/api/types'
import ModelSettingsPage from '../src/pages/ModelSettingsPage'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/profiles', () => ({
  profileKeys: {
    profiles: () => ['model-profiles'],
    protocols: () => ['model-profiles', 'protocols'],
  },
  fetchProfiles: vi.fn(),
  fetchProtocols: vi.fn(),
  createProfile: vi.fn(),
  updateProfile: vi.fn(),
  testConnection: vi.fn(),
  deleteProfile: vi.fn(),
}))

const SECRET = 'sk-secret-in-form'

const PROTOCOLS: ProtocolCapabilitiesOut[] = [
  {
    protocol: 'anthropic-messages', supports_json_schema: false, supports_json_object: false,
    supports_temperature: true, supports_max_tokens: true, requires_api_key: false,
    notes: 'Anthropic Messages API',
  },
  {
    protocol: 'chat-completions-compatible',
    supports_json_schema: false,
    supports_json_object: true,
    supports_temperature: true,
    supports_max_tokens: true,
    requires_api_key: false,
    notes: 'OpenAI 兼容 /chat/completions。Base URL 填 API 根路径，适配器自行追加端点。',
  },
  {
    protocol: 'fake-provider',
    supports_json_schema: true,
    supports_json_object: true,
    supports_temperature: false,
    supports_max_tokens: false,
    requires_api_key: false,
    notes: '仅供测试/演示；不会发起任何网络请求。',
  },
] as ProtocolCapabilitiesOut[]

function profile(overrides: Partial<ModelProfileOut> = {}): ModelProfileOut {
  return {
    id: 'p1',
    name: '本地网关',
    protocol: 'chat-completions-compatible',
    base_url: 'https://api.example.com/v1',
    model: 'example-chat-model',
    params: { temperature: 0.2 },
    credential_mode: 'session',
    has_key: true,
    version: 1,
    created_at: '2026-09-28T00:00:00+00:00',
    updated_at: '2026-09-28T00:00:00+00:00',
    credential_warning: null,
    ...overrides,
  } as ModelProfileOut
}

async function fillNewProfile() {
  await userEvent.type(screen.getByTestId('profile-name'), '本地网关')
  await userEvent.type(screen.getByTestId('profile-base-url'), 'https://api.example.com/v1')
  await userEvent.type(screen.getByTestId('profile-model'), 'example-chat-model')
  await userEvent.type(screen.getByTestId('profile-api-key'), SECRET)
}

async function showSavedProfiles() {
  await userEvent.click(screen.getByTestId('model-view-saved'))
  expect(screen.getByTestId('model-saved-panel')).toBeVisible()
}

async function showEditor() {
  await userEvent.click(screen.getByTestId('model-view-editor'))
  expect(screen.getByTestId('model-editor-panel')).toBeVisible()
}

async function savedAction(action: 'edit' | 'test' | 'delete', id = 'p1') {
  await showSavedProfiles()
  const button = await screen.findByTestId(`profile-${action}-${id}`)
  expect(button).toBeVisible()
  await userEvent.click(button)
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

const TEST_SUCCESS: ConnectionTestOut = {
  ok: true, protocol: 'chat-completions-compatible', model: 'example-chat-model',
  adapter: 'chat-completions', detail: '连接成功', latency_ms: 5,
  usage: null, usage_unknown: true, error_code: null, run_id: 'run-view-switch',
}

describe('ModelSettingsPage', () => {
  beforeEach(() => {
    vi.mocked(profilesApi.fetchProfiles).mockReset()
    vi.mocked(profilesApi.fetchProtocols).mockReset()
    vi.mocked(profilesApi.createProfile).mockReset()
    vi.mocked(profilesApi.updateProfile).mockReset()
    vi.mocked(profilesApi.testConnection).mockReset()
    vi.mocked(profilesApi.deleteProfile).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
    vi.mocked(profilesApi.fetchProtocols).mockResolvedValue(PROTOCOLS)
  })

  it('新增和编辑配置都标示必填字段，不将密钥或参数标为必填', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    renderWithProviders(<ModelSettingsPage />)
    await screen.findByTestId('profile-card')
    const checkLabels = () => {
      expect(screen.getByText('* 为必填项')).toBeVisible()
      expect(screen.getByLabelText('* 配置名称（Profile Name）')).toBeRequired()
      expect(screen.getByLabelText('* 协议（API Protocol）')).toHaveValue('chat-completions-compatible')
      expect(screen.getByLabelText('* 服务地址（Base URL）')).toBeRequired()
      expect(screen.getByLabelText('* 模型名（Model ID）')).toBeRequired()
      expect(screen.getByTestId('profile-max_tokens')).not.toBeRequired()
    }
    checkLabels()
    expect(screen.getByLabelText('接口密钥（API Key，可选）')).not.toBeRequired()
    await savedAction('edit')
    checkLabels()
    expect(screen.getByTestId('key-action-keep')).toBeChecked()
    await waitFor(() => expect(document.getElementById('profile-editor')).toHaveFocus())
  })

  it('默认新建，两个视图保留挂载及完整草稿，切换只改变展示', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.type(screen.getByTestId('profile-max_tokens'), '16000')
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-mode'), 'adaptive')
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-effort'), 'high')
    const name = screen.getByTestId('profile-name')
    const editor = screen.getByTestId('model-editor-panel')
    const saved = screen.getByTestId('model-saved-panel')
    expect(editor).toBeVisible()
    expect(saved).not.toBeVisible()
    expect(screen.getByTestId('model-view-editor')).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByTestId('model-view-saved')).toHaveAttribute('aria-pressed', 'false')
    await showSavedProfiles()
    expect(screen.getByTestId('model-editor-panel')).toBe(editor)
    expect(editor).not.toBeVisible()
    expect(screen.getByTestId('model-view-saved')).toHaveAttribute('aria-pressed', 'true')
    await showEditor()
    expect(screen.getByTestId('profile-name')).toBe(name)
    expect(name).toHaveValue('本地网关')
    expect(screen.getByTestId('profile-api-key')).toHaveValue(SECRET)
    expect(screen.getByTestId('profile-max_tokens')).toHaveValue(16000)
    expect(screen.getByTestId('profile-thinking-mode')).toHaveValue('adaptive')
    expect(screen.getByTestId('profile-thinking-effort')).toHaveValue('high')
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
    expect(profilesApi.testConnection).not.toHaveBeenCalled()
  })

  it('旧列表锚点直接显示查看配置，其他表单锚点显示新建', async () => {
    const page = renderWithProviders(<ModelSettingsPage />, '/settings/models#saved-profiles')
    expect(screen.getByTestId('model-saved-panel')).toBeVisible()
    expect(screen.getByTestId('model-editor-panel')).not.toBeVisible()
    page.unmount()
    renderWithProviders(<ModelSettingsPage />, '/settings/models#profile-thinking')
    expect(screen.getByTestId('model-editor-panel')).toBeVisible()
    expect(screen.getByTestId('profile-thinking-mode')).toBeVisible()
    expect(screen.getByTestId('profile-thinking-effort')).toBeVisible()
  })

  it('新建表单所有常用字段及思考选项常显，并提供悬停说明', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await screen.findByRole('option', { name: 'OpenAI 协议（Chat Completions）' })
    for (const id of [
      'profile-name', 'profile-protocol', 'profile-base-url', 'profile-model',
      'profile-max_tokens', 'profile-temperature', 'profile-top_p', 'profile-timeout_seconds',
      'profile-thinking-mode', 'profile-thinking-effort', 'profile-api-key',
      'credential-mode-session', 'credential-mode-system', 'credential-mode-none',
    ]) {
      const field = screen.getByTestId(id)
      expect(field).toBeVisible()
      expect(field).toHaveAttribute('title', expect.stringMatching(/\S.+/))
    }
    expect(screen.getByTestId('profile-params')).toHaveAttribute('readonly')
  })

  it('编辑切换视图保留修改，取消编辑清空密钥并返回查看', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('edit')
    expect(screen.getByTestId('model-view-editor')).toHaveTextContent('编辑配置')
    expect(screen.getByTestId('model-view-editor')).toHaveAttribute('aria-pressed', 'true')
    await userEvent.clear(screen.getByTestId('profile-name'))
    await userEvent.type(screen.getByTestId('profile-name'), '尚未保存的编辑')
    await userEvent.click(screen.getByTestId('key-action-replace'))
    await userEvent.type(screen.getByTestId('profile-api-key'), SECRET)
    await showSavedProfiles()
    await showEditor()
    expect(screen.getByTestId('profile-name')).toHaveValue('尚未保存的编辑')
    expect(screen.getByTestId('key-action-replace')).toBeChecked()
    expect(screen.getByTestId('profile-api-key')).toHaveValue(SECRET)
    await userEvent.click(screen.getByTestId('profile-cancel'))
    expect(screen.getByTestId('model-saved-panel')).toBeVisible()
    expect(screen.getByTestId('model-view-editor')).toHaveTextContent('新建配置')
    await showEditor()
    expect(screen.getByTestId('profile-name')).toHaveValue('')
    expect(screen.getByTestId('profile-api-key')).toHaveValue('')
    expect(screen.queryByTestId('key-action-keep')).not.toBeInTheDocument()
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
  })

  it('空状态说明可配置，并提示连接测试的入口', async () => {
    renderWithProviders(<ModelSettingsPage />)

    await showSavedProfiles()
    expect(await screen.findByTestId('profiles-empty')).toBeVisible()
    expect(screen.getByTestId('profiles-empty')).toHaveTextContent(/新建配置/)
    await showEditor()
    await waitFor(() => expect(profilesApi.fetchProtocols).toHaveBeenCalled())
    expect(within(screen.getByTestId('model-editor-panel')).getByText(/连接测试会请求模型服务，可能产生费用/)).toBeVisible()
    const protocol = screen.getByTestId('profile-protocol')
    expect(protocol).toHaveAttribute('title', expect.stringContaining('云端服务通常需要 API 密钥'))
    expect(protocol).toHaveAttribute('title', expect.stringContaining('明确无需认证的本地服务'))
    expect(protocol.title).not.toContain('https://api.anthropic.com/v1')
    expect(protocol.title).not.toContain('json_object')
    expect(protocol.title).not.toContain('json_schema')
    await userEvent.selectOptions(screen.getByTestId('profile-protocol'), 'fake-provider')
    expect(protocol).toHaveAttribute('title', expect.stringContaining('用于本地模拟界面流程，返回测试数据'))
    expect(protocol.title).not.toContain('云端服务通常需要 API 密钥')
    expect(protocol.title).not.toContain('https://api.anthropic.com/v1')
  })

  it('shows Chinese protocol and credential labels without changing saved identifiers', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    renderWithProviders(<ModelSettingsPage />)
    await showSavedProfiles()
    const card = await screen.findByTestId('profile-card')
    expect(card).toHaveTextContent('OpenAI 协议（Chat Completions）')
    expect(card).toHaveTextContent('仅本次会话')
    expect(card).not.toHaveTextContent('chat-completions-compatible')
    expect(card).toHaveTextContent('https://api.example.com/v1')
    await showEditor()
    expect(screen.getByTestId('profile-protocol')).toHaveValue('chat-completions-compatible')
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
    expect(profilesApi.testConnection).not.toHaveBeenCalled()
  })

  it('offers Anthropic with matching help and saves its original protocol identifier', async () => {
    vi.mocked(profilesApi.createProfile).mockResolvedValue(profile({ protocol: 'anthropic-messages' }))
    renderWithProviders(<ModelSettingsPage />)
    await screen.findByRole('option', { name: 'Anthropic 协议（Messages）' })
    await userEvent.selectOptions(screen.getByTestId('profile-protocol'), 'anthropic-messages')
    const help = screen.getByTestId('profile-protocol').title
    expect(help).toContain('https://api.anthropic.com/v1')
    expect(help).toContain('官方服务需要 API 密钥')
    expect(help).toContain('手动思考要求最大输出 Token 大于 1024')
    expect(help).not.toContain('/chat/completions')
    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-save'))
    await waitFor(() => expect(profilesApi.createProfile).toHaveBeenCalledWith(expect.objectContaining({
      protocol: 'anthropic-messages', api_key: SECRET,
    })))
  })

  it('新建配置时提交密钥，但界面上不回显密钥', async () => {
    vi.mocked(profilesApi.createProfile).mockResolvedValue(profile())
    // 创建成功后列表刷新会拿到新配置
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValueOnce([]).mockResolvedValue([profile()])
    renderWithProviders(<ModelSettingsPage />)

    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-save'))

    await waitFor(() => expect(profilesApi.createProfile).toHaveBeenCalledTimes(1))
    const payload = vi.mocked(profilesApi.createProfile).mock.calls[0][0]
    expect(payload).toMatchObject({
      name: '本地网关',
      base_url: 'https://api.example.com/v1',
      model: 'example-chat-model',
      credential_mode: 'session',
      api_key: SECRET,
    })

    expect(await screen.findByTestId('settings-notice')).toHaveTextContent('已保存配置')
    expect(screen.getByTestId('settings-notice')).toBeVisible()
    expect(screen.getByTestId('model-saved-panel')).toBeVisible()
    // 表单重置 + 列表只显示 has_key：密钥不出现在 DOM 里
    expect(document.body.textContent).not.toContain(SECRET)
    expect(screen.getByTestId('profile-card')).toHaveTextContent('已保存密钥')
    await showEditor()
    expect(screen.getByTestId('profile-api-key')).toHaveValue('')
  })

  it('原始参数只读，常用参数超出范围时不能提交', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    expect(screen.getByTestId('profile-params')).toHaveAttribute('readonly')
    await userEvent.type(screen.getByTestId('profile-temperature'), '3')

    await userEvent.click(screen.getByTestId('profile-save'))

    expect(profilesApi.createProfile).not.toHaveBeenCalled()
  })

  it('常用参数表单可调整输出预算与超时，清空则恢复默认', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()

    await userEvent.type(screen.getByTestId('profile-max_tokens'), '16000')
    await userEvent.type(screen.getByTestId('profile-timeout_seconds'), '300')
    let parsed = JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)
    expect(parsed.max_tokens).toBe(16000)
    expect(parsed.timeout_seconds).toBe(300)
    expect(parsed.thinking).toBeUndefined()

    await userEvent.clear(screen.getByTestId('profile-max_tokens'))
    parsed = JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)
    expect(parsed.max_tokens).toBeUndefined()
    expect(parsed.timeout_seconds).toBe(300)
    expect(screen.queryByTestId('thinking-enabled')).not.toBeInTheDocument()
  })

  it('编辑时“保持不变”不发送密钥字段', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(profile({ version: 2 }))
    renderWithProviders(<ModelSettingsPage />)

    await savedAction('edit')
    expect(screen.getByTestId('key-action-keep')).toBeChecked()
    await userEvent.click(screen.getByTestId('profile-save'))

    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(profilesApi.updateProfile).mock.calls[0]
    expect(payload.api_key).toBeNull()
    expect(payload.remove_api_key).toBe(false)
    expect(payload.credential_mode).toBeNull()
  })

  it('替换密钥按所选保存方式提交，成功后清除输入', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(profile({ credential_mode: 'system', version: 2 }))
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('edit')
    await userEvent.click(screen.getByTestId('key-action-replace'))
    await userEvent.click(screen.getByTestId('credential-mode-system'))
    await userEvent.type(screen.getByTestId('profile-api-key'), SECRET)
    await userEvent.click(screen.getByTestId('profile-save'))
    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalledWith('p1', expect.objectContaining({
      api_key: SECRET, credential_mode: 'system', remove_api_key: false,
    })))
    expect(screen.getByTestId('model-saved-panel')).toBeVisible()
    expect(screen.getByTestId('settings-notice')).toBeVisible()
    await showEditor()
    expect(screen.getByTestId('profile-api-key')).toHaveValue('')
  })

  it('常用参数编辑保留已有提供方专用参数，并验证连接草稿范围', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile({ params: { custom: 'keep', thinking: { type: 'adaptive' }, temperature: 0.2 } })])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(profile())
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('edit')
    await userEvent.clear(screen.getByTestId('profile-temperature'))
    await userEvent.type(screen.getByTestId('profile-temperature'), '3')
    await userEvent.click(screen.getByTestId('profile-test-draft'))
    expect(await screen.findByTestId('settings-error')).toHaveTextContent('超出允许范围')
    expect(profilesApi.testConnection).not.toHaveBeenCalled()
    await userEvent.clear(screen.getByTestId('profile-temperature'))
    await userEvent.type(screen.getByTestId('profile-temperature'), '0.3')
    await userEvent.click(screen.getByTestId('profile-save'))
    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalled())
    expect(vi.mocked(profilesApi.updateProfile).mock.calls[0][1].params).toEqual({ custom: 'keep', thinking: { type: 'adaptive' }, temperature: 0.3 })
  })

  it('保存、回显和清除模型默认思考设置，不自动增加预算', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile({ params: { custom: 'keep', thinking: { type: 'adaptive' }, reasoning_effort: 'high' } })])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(profile())
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('edit')
    expect(screen.getByTestId('profile-thinking-mode')).toHaveValue('adaptive')
    expect(screen.getByTestId('profile-thinking-effort')).toHaveValue('high')
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-mode'), 'enabled')
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-effort'), 'low')
    await userEvent.click(screen.getByTestId('profile-save'))
    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1))
    expect(vi.mocked(profilesApi.updateProfile).mock.calls[0][1].params).toEqual({ custom: 'keep', thinking: { type: 'enabled' }, reasoning_effort: 'low' })
    await savedAction('edit')
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-mode'), 'disabled')
    expect(screen.getByTestId('profile-thinking-effort')).toBeDisabled()
    expect(JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)).toEqual({ custom: 'keep', thinking: { type: 'disabled' } })
    await userEvent.selectOptions(screen.getByTestId('profile-thinking-mode'), 'default')
    expect(screen.getByTestId('profile-thinking-effort')).not.toBeDisabled()
    expect(JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)).toEqual({ custom: 'keep' })
  })

  it('编辑时“清除密钥”发送 remove_api_key', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(
      profile({ has_key: false, credential_mode: 'none', version: 2 }),
    )
    renderWithProviders(<ModelSettingsPage />)

    await savedAction('edit')
    await userEvent.click(screen.getByTestId('key-action-remove'))
    await userEvent.click(screen.getByTestId('profile-save'))

    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(profilesApi.updateProfile).mock.calls[0]
    expect(payload.remove_api_key).toBe(true)
    expect(await screen.findByTestId('settings-notice')).toHaveTextContent('未保存密钥')
  })

  it('版本冲突时提示并刷新列表', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockRejectedValue(
      new ApiError(409, {
        code: 'VERSION_CONFLICT',
        message: 'ModelProfile 版本冲突',
        details: { current_version: 3, expected_version: 1 },
      }),
    )
    renderWithProviders(<ModelSettingsPage />)

    await savedAction('edit')
    await userEvent.click(screen.getByTestId('profile-save'))

    expect(await screen.findByTestId('settings-error')).toHaveTextContent('已被其它操作更新')
    await waitFor(() => expect(profilesApi.fetchProfiles).toHaveBeenCalledTimes(2))
  })

  it('后端错误信息会展示给用户（例如系统凭据库降级警告）', async () => {
    vi.mocked(profilesApi.createProfile).mockResolvedValue(
      profile({ credential_warning: '系统凭据库不可用，密钥改为仅在本会话内保存。' }),
    )
    renderWithProviders(<ModelSettingsPage />)

    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-save'))

    expect(await screen.findByTestId('settings-notice')).toHaveTextContent('系统凭据库不可用')
  })

  it('删除配置后列表回到空状态', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValueOnce([profile()]).mockResolvedValue([])
    vi.mocked(profilesApi.deleteProfile).mockResolvedValue(undefined)
    renderWithProviders(<ModelSettingsPage />)

    await savedAction('delete')

    await waitFor(() => expect(profilesApi.deleteProfile).toHaveBeenCalledWith('p1'))
    expect(await screen.findByTestId('settings-notice')).toHaveTextContent('凭据引用也已清理')
    expect(screen.getByTestId('settings-notice')).toBeVisible()
    expect(await screen.findByTestId('profiles-empty')).toBeVisible()
  })

  it('保存进行中跨视图锁定表单和列表写操作，成功后返回查看并解锁', async () => {
    const pending = deferred<ModelProfileOut>()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile({ name: '已保存模型' })])
    vi.mocked(profilesApi.createProfile).mockReturnValue(pending.promise)
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-save'))
    await waitFor(() => expect(profilesApi.createProfile).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('profile-name')).toBeDisabled()
    expect(screen.getByTestId('profile-thinking-mode')).toBeDisabled()
    expect(screen.getByTestId('profile-api-key')).toBeDisabled()
    expect(screen.getByTestId('profile-save')).toBeDisabled()
    expect(screen.getByTestId('profile-test-draft')).toBeDisabled()
    await showSavedProfiles()
    for (const action of ['edit', 'delete', 'test']) {
      expect(screen.getByTestId(`profile-${action}-p1`)).toBeDisabled()
    }
    await showEditor()
    expect(screen.getByTestId('profile-name')).toBeDisabled()
    await act(async () => pending.resolve(profile({ id: 'created' })))
    await waitFor(() => expect(screen.getByTestId('model-saved-panel')).toBeVisible())
    expect(screen.getByTestId('settings-notice')).toBeVisible()
    expect(screen.getByTestId('profile-edit-p1')).toBeEnabled()
    await showEditor()
    expect(screen.getByTestId('profile-name')).toBeEnabled()
    expect(screen.getByTestId('profile-name')).toHaveValue('')
    expect(screen.getByTestId('profile-api-key')).toHaveValue('')
  })

  it('保存失败反馈在查看视图可见，回到新建后仍有原草稿', async () => {
    const pending = deferred<ModelProfileOut>()
    vi.mocked(profilesApi.createProfile).mockReturnValue(pending.promise)
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-save'))
    await showSavedProfiles()
    await act(async () => pending.reject(new Error('配置保存失败，请重试')))
    expect(await screen.findByTestId('settings-error')).toBeVisible()
    expect(screen.getByTestId('settings-error')).toHaveTextContent('配置保存失败，请重试')
    await showEditor()
    expect(screen.getByTestId('profile-name')).toHaveValue('本地网关')
    expect(screen.getByTestId('profile-api-key')).toHaveValue(SECRET)
    expect(screen.getByTestId('profile-save')).toBeEnabled()
  })

  it('删除其他配置时锁定编辑，完成后保留隐藏的新建草稿和密钥', async () => {
    const pending = deferred<void>()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValueOnce([profile()]).mockResolvedValue([])
    vi.mocked(profilesApi.deleteProfile).mockReturnValue(pending.promise)
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.type(screen.getByTestId('profile-max_tokens'), '8000')
    await savedAction('delete')
    expect(screen.getByTestId('profile-delete-p1')).toBeDisabled()
    expect(screen.getByTestId('profile-edit-p1')).toBeDisabled()
    await showEditor()
    expect(screen.getByTestId('profile-name')).toBeDisabled()
    expect(screen.getByTestId('profile-test-draft')).toBeDisabled()
    await act(async () => pending.resolve())
    await waitFor(() => expect(screen.getByTestId('profile-name')).toBeEnabled())
    expect(screen.getByTestId('profile-name')).toHaveValue('本地网关')
    expect(screen.getByTestId('profile-api-key')).toHaveValue(SECRET)
    expect(screen.getByTestId('profile-max_tokens')).toHaveValue(8000)
    expect(screen.getByTestId('settings-notice')).toBeVisible()
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
  })

  it('被任务引用的配置删除失败时保留列表并展示具体原因', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.deleteProfile).mockRejectedValue(new ApiError(409, {
      code: 'RESOURCE_CONFLICT', message: '该配置已被任务引用，不能删除',
    }))
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('delete')
    expect(await screen.findByTestId('settings-error')).toBeVisible()
    expect(screen.getByTestId('settings-error')).toHaveTextContent('该配置已被任务引用')
    expect(screen.getByTestId('profile-card')).toBeVisible()
    expect(screen.getByTestId('profile-delete-p1')).toBeEnabled()
  })
})

describe('ModelSettingsPage / 连接测试', () => {
  beforeEach(() => {
    vi.mocked(profilesApi.fetchProfiles).mockReset()
    vi.mocked(profilesApi.fetchProtocols).mockReset()
    vi.mocked(profilesApi.createProfile).mockReset()
    vi.mocked(profilesApi.updateProfile).mockReset()
    vi.mocked(profilesApi.testConnection).mockReset()
    vi.mocked(profilesApi.deleteProfile).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
    vi.mocked(profilesApi.fetchProtocols).mockResolvedValue(PROTOCOLS)
  })
  it('测试已保存配置：显示成功、用量未知与“未评估效果”说明', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.testConnection).mockResolvedValue({
      ok: true,
      protocol: 'chat-completions-compatible',
      model: 'example-chat-model',
      adapter: 'chat-completions',
      detail: '连接成功，且返回结构可解析（未评估小说标注效果）。',
      latency_ms: 123,
      usage: { input_tokens: 12, output_tokens: 8, total_tokens: 20, unknown: false },
      usage_unknown: false,
      error_code: null,
      run_id: 'run-1',
    })

    renderWithProviders(<ModelSettingsPage />)
    await savedAction('test')

    const result = await screen.findByTestId('connection-result')
    expect(result).toBeVisible()
    await userEvent.click(within(result).getByText('连接详情'))
    expect(result).toHaveTextContent('连接成功')
    expect(result).toHaveTextContent('123 ms')
    expect(result).toHaveTextContent('合计 20')
    expect(result).toHaveTextContent('协议 / 模型：OpenAI 协议（Chat Completions） / example-chat-model')
    expect(result).not.toHaveTextContent('chat-completions-compatible')
    expect(result).toHaveTextContent('未评估小说标注效果')
    expect(profilesApi.testConnection).toHaveBeenCalledWith({ profile_id: 'p1' })
  })

  it('失败结果展示稳定错误码，不显示成功文案', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.testConnection).mockResolvedValue({
      ok: false,
      protocol: 'chat-completions-compatible',
      model: 'example-chat-model',
      adapter: 'chat-completions',
      detail: 'PROVIDER_AUTH_FAILED: 提供方返回 401',
      latency_ms: null,
      usage: null,
      usage_unknown: true,
      error_code: 'PROVIDER_AUTH_FAILED',
      run_id: 'run-2',
    })

    renderWithProviders(<ModelSettingsPage />)
    await savedAction('test')

    const result = await screen.findByTestId('connection-result')
    expect(result).toBeVisible()
    await userEvent.click(within(result).getByText('连接详情'))
    expect(result).toHaveTextContent('连接失败')
    expect(result).toHaveTextContent('PROVIDER_AUTH_FAILED')
    expect(result).toHaveTextContent('未知（模型服务未返回用量）')
  })

  it('FakeProvider 结果会被明确标注为测试适配器', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile({ protocol: 'fake-provider' })])
    vi.mocked(profilesApi.testConnection).mockResolvedValue({
      ok: true,
      protocol: 'fake-provider',
      model: 'fake-model',
      adapter: 'fake-provider',
      detail: 'FakeProvider 连接成功（仅测试用，未访问任何网络）。',
      latency_ms: 0,
      usage: { unknown: true },
      usage_unknown: true,
      error_code: null,
      run_id: 'run-3',
    })

    renderWithProviders(<ModelSettingsPage />)
    await savedAction('test')

    expect(await screen.findByTestId('fake-provider-warning')).toHaveTextContent('本地模拟测试完成')
  })

  it('可以先用草稿测试（不保存配置），密钥只在请求体里', async () => {
    const pending = deferred<ConnectionTestOut>()
    vi.mocked(profilesApi.testConnection).mockReturnValue(pending.promise)

    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-test-draft'))

    await waitFor(() => expect(profilesApi.testConnection).toHaveBeenCalled())
    const payload = vi.mocked(profilesApi.testConnection).mock.calls[0][0]
    expect(payload.profile_id).toBeUndefined()
    expect(payload.draft).toMatchObject({ protocol: 'chat-completions-compatible', api_key: SECRET })
    await userEvent.clear(screen.getByTestId('profile-name'))
    await userEvent.type(screen.getByTestId('profile-name'), '后续草稿名称')
    await showSavedProfiles()
    await act(async () => pending.resolve(TEST_SUCCESS))
    const result = await screen.findByTestId('connection-result')
    expect(result).toBeVisible()
    expect(result).toHaveTextContent('填写内容「本地网关」')
    expect(result).not.toHaveTextContent('后续草稿名称')
    expect(profilesApi.testConnection).toHaveBeenCalledTimes(1)
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
    // 测试不会把密钥写进页面
    expect(document.body.textContent).not.toContain(SECRET)
  })

  it('连接测试跨视图继续等待且只发一次请求，反馈保留发起时的目标', async () => {
    const pending = deferred<ConnectionTestOut>()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.testConnection).mockReturnValue(pending.promise)
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('test')
    expect(screen.getByTestId('profile-test-p1')).toBeDisabled()
    await showEditor()
    expect(screen.getByTestId('profile-test-draft')).toBeDisabled()
    await userEvent.type(screen.getByTestId('profile-name'), '后来的新草稿')
    await userEvent.type(screen.getByTestId('profile-model'), 'different-model')
    expect(profilesApi.testConnection).toHaveBeenCalledTimes(1)
    expect(profilesApi.testConnection).toHaveBeenCalledWith({ profile_id: 'p1' })
    await act(async () => pending.resolve(TEST_SUCCESS))
    const result = await screen.findByTestId('connection-result')
    expect(result).toBeVisible()
    expect(result).toHaveTextContent('本地网关')
    expect(result).not.toHaveTextContent('后来的新草稿')
    expect(screen.getByTestId('profile-name')).toHaveValue('后来的新草稿')
    await userEvent.click(within(result).getByText('连接详情'))
    expect(result).toHaveTextContent('example-chat-model')
    expect(result).not.toHaveTextContent('different-model')
    expect(screen.getByTestId('profile-test-draft')).toBeEnabled()
    await showSavedProfiles()
    expect(result).toBeVisible()
    expect(screen.getByTestId('profile-test-p1')).toBeEnabled()
    expect(profilesApi.testConnection).toHaveBeenCalledTimes(1)
  })

  it('编辑时测试当前填写内容只使用当前输入，不借用已保存密钥', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.testConnection).mockResolvedValue(TEST_SUCCESS)
    renderWithProviders(<ModelSettingsPage />)
    await savedAction('edit')
    expect(screen.getByTestId('key-action-keep')).toBeChecked()
    await userEvent.click(screen.getByTestId('profile-test-draft'))
    await waitFor(() => expect(profilesApi.testConnection).toHaveBeenCalledWith({
      draft: expect.objectContaining({ api_key: null, model: 'example-chat-model' }),
    }))
    expect(profilesApi.updateProfile).not.toHaveBeenCalled()
    expect(await screen.findByTestId('connection-result')).toHaveTextContent('填写内容「本地网关」')
  })
})
