import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../src/api/client'
import * as profilesApi from '../src/api/profiles'
import type { ModelProfileOut, ProtocolCapabilitiesOut } from '../src/api/types'
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

  it('空状态说明可配置，并提示连接测试的入口', async () => {
    renderWithProviders(<ModelSettingsPage />)

    expect(await screen.findByTestId('profiles-empty')).toHaveTextContent('不需要修改源码')
    await waitFor(() => expect(profilesApi.fetchProtocols).toHaveBeenCalled())
    expect(screen.getByText(/保存前可以用/)).toBeInTheDocument()
    expect(await screen.findByTestId('protocol-capabilities')).toHaveTextContent('json_schema：未声明')
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
    // 表单重置 + 列表只显示 has_key：密钥不出现在 DOM 里
    expect(document.body.textContent).not.toContain(SECRET)
    expect(screen.getByTestId('profile-card')).toHaveTextContent('已保存密钥')
  })

  it('生成参数不是合法 JSON 时本地拦截，不发起请求', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.clear(screen.getByTestId('profile-params'))
    await userEvent.type(screen.getByTestId('profile-params'), '{{')

    await userEvent.click(screen.getByTestId('profile-save'))

    expect(await screen.findByTestId('settings-error')).toHaveTextContent('不是合法 JSON')
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
  })

  it('思考模式按钮写入 thinking.type，并补足输出预算与超时', async () => {
    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()

    await userEvent.click(screen.getByTestId('thinking-adaptive'))
    let parsed = JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)
    expect(parsed.thinking).toEqual({ type: 'adaptive' })
    expect(parsed.max_tokens).toBeGreaterThanOrEqual(32000)
    expect(parsed.timeout_seconds).toBeGreaterThanOrEqual(300)

    await userEvent.click(screen.getByTestId('thinking-disabled'))
    parsed = JSON.parse((screen.getByTestId('profile-params') as HTMLTextAreaElement).value)
    expect(parsed.thinking).toEqual({ type: 'disabled' })
    // 关闭思考不修改已有预算（保留上一次写入的值）
    expect(parsed.max_tokens).toBeGreaterThanOrEqual(32000)

    // 非法 JSON 时给出本地错误，不写入
    await userEvent.clear(screen.getByTestId('profile-params'))
    await userEvent.type(screen.getByTestId('profile-params'), 'not-json')
    await userEvent.click(screen.getByTestId('thinking-enabled'))
    expect(await screen.findByTestId('settings-error')).toHaveTextContent('不是合法 JSON')
  })

  it('编辑时“保持不变”不发送密钥字段', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(profile({ version: 2 }))
    renderWithProviders(<ModelSettingsPage />)

    await userEvent.click(await screen.findByTestId('profile-edit-p1'))
    expect(screen.getByTestId('key-action-keep')).toBeChecked()
    await userEvent.click(screen.getByTestId('profile-save'))

    await waitFor(() => expect(profilesApi.updateProfile).toHaveBeenCalledTimes(1))
    const [, payload] = vi.mocked(profilesApi.updateProfile).mock.calls[0]
    expect(payload.api_key).toBeNull()
    expect(payload.remove_api_key).toBe(false)
    expect(payload.credential_mode).toBeNull()
  })

  it('编辑时“清除密钥”发送 remove_api_key', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.updateProfile).mockResolvedValue(
      profile({ has_key: false, credential_mode: 'none', version: 2 }),
    )
    renderWithProviders(<ModelSettingsPage />)

    await userEvent.click(await screen.findByTestId('profile-edit-p1'))
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

    await userEvent.click(await screen.findByTestId('profile-edit-p1'))
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
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile()])
    vi.mocked(profilesApi.deleteProfile).mockResolvedValue(undefined)
    renderWithProviders(<ModelSettingsPage />)

    await userEvent.click(await screen.findByTestId('profile-delete-p1'))

    await waitFor(() => expect(profilesApi.deleteProfile).toHaveBeenCalledWith('p1'))
    expect(await screen.findByTestId('settings-notice')).toHaveTextContent('凭据引用也已清理')
  })
})

describe('ModelSettingsPage / 连接测试（T07）', () => {
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
    await userEvent.click(await screen.findByTestId('profile-test-p1'))

    const result = await screen.findByTestId('connection-result')
    expect(result).toHaveTextContent('连接成功')
    expect(result).toHaveTextContent('123 ms')
    expect(result).toHaveTextContent('合计 20')
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
    await userEvent.click(await screen.findByTestId('profile-test-p1'))

    const result = await screen.findByTestId('connection-result')
    expect(result).toHaveTextContent('连接失败')
    expect(result).toHaveTextContent('PROVIDER_AUTH_FAILED')
    expect(result).toHaveTextContent('未知（提供方未返回 usage，不按 0 计）')
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
    await userEvent.click(await screen.findByTestId('profile-test-p1'))

    expect(await screen.findByTestId('fake-provider-warning')).toHaveTextContent('没有访问任何真实服务')
  })

  it('可以先用草稿测试（不保存配置），密钥只在请求体里', async () => {
    vi.mocked(profilesApi.testConnection).mockResolvedValue({
      ok: true,
      protocol: 'chat-completions-compatible',
      model: 'example-chat-model',
      adapter: 'chat-completions',
      detail: '连接成功',
      latency_ms: 5,
      usage: { unknown: true },
      usage_unknown: true,
      error_code: null,
      run_id: 'run-4',
    })

    renderWithProviders(<ModelSettingsPage />)
    await fillNewProfile()
    await userEvent.click(screen.getByTestId('profile-test-draft'))

    await waitFor(() => expect(profilesApi.testConnection).toHaveBeenCalled())
    const payload = vi.mocked(profilesApi.testConnection).mock.calls[0][0]
    expect(payload.profile_id).toBeUndefined()
    expect(payload.draft).toMatchObject({ protocol: 'chat-completions-compatible', api_key: SECRET })
    expect(profilesApi.createProfile).not.toHaveBeenCalled()
    // 测试不会把密钥写进页面
    expect(document.body.textContent).not.toContain(SECRET)
  })
})