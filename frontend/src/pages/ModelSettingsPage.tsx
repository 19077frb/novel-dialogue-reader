import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { ApiError } from '../api/client'
import {
  createProfile,
  deleteProfile,
  fetchProfiles,
  fetchProtocols,
  profileKeys,
  testConnection,
  updateProfile,
} from '../api/profiles'
import type { ConnectionTestOut, CredentialMode, ModelProfileOut } from '../api/types'

type KeyAction = 'keep' | 'replace' | 'remove'

interface FormState {
  name: string
  protocol: string
  baseUrl: string
  model: string
  paramsText: string
  credentialMode: CredentialMode
  apiKey: string
}

const EMPTY_FORM: FormState = {
  name: '',
  protocol: 'chat-completions-compatible',
  baseUrl: '',
  model: '',
  paramsText: '{}',
  credentialMode: 'session',
  apiKey: '',
}

function formFromProfile(profile: ModelProfileOut): FormState {
  return {
    name: profile.name,
    protocol: profile.protocol,
    baseUrl: profile.base_url,
    model: profile.model,
    paramsText: JSON.stringify(profile.params ?? {}, null, 2),
    credentialMode: profile.credential_mode,
    apiKey: '',
  }
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const details = error.details ?? {}
    const extra = Object.entries(details)
      .filter(([key]) => key !== 'errors')
      .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
      .join('，')
    return extra ? `${error.message}（${extra}）` : error.message
  }
  if (error instanceof Error) return error.message
  return '未知错误'
}

export default function ModelSettingsPage() {
  const queryClient = useQueryClient()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [keyAction, setKeyAction] = useState<KeyAction>('replace')
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{ target: string; data: ConnectionTestOut } | null>(null)

  const profiles = useQuery({
    queryKey: profileKeys.profiles(),
    queryFn: ({ signal }) => fetchProfiles(signal),
  })
  const protocols = useQuery({
    queryKey: profileKeys.protocols(),
    queryFn: ({ signal }) => fetchProtocols(signal),
  })

  const selectedProtocol = protocols.data?.find((item) => item.protocol === form.protocol)

  function resetForm() {
    setEditingId(null)
    setForm(EMPTY_FORM)
    setKeyAction('replace')
  }

  function parseParams(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(form.paramsText || '{}')
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        setError('生成参数必须是 JSON 对象，例如 {"temperature": 0.2}')
        return null
      }
      for (const [key, min, max, integer] of [
        ['max_tokens', 1, Infinity, true], ['timeout_seconds', 1, Infinity, true],
        ['temperature', 0, 2, false], ['top_p', 0, 1, false],
      ] as const) {
        const value = parsed[key]
        if (value !== undefined && (typeof value !== 'number' || !Number.isFinite(value)
          || value < min || value > max || (integer && !Number.isSafeInteger(value)))) {
          setError(`${key} 超出允许范围，请检查常用生成参数。`)
          return null
        }
      }
      return parsed as Record<string, unknown>
    } catch {
      setError('生成参数不是合法 JSON')
      return null
    }
  }

  function changeParameter(key: string, value: string) {
    const params = JSON.parse(form.paramsText) as Record<string, unknown>
    if (value === '') delete params[key]
    else params[key] = Number(value)
    setForm({ ...form, paramsText: JSON.stringify(params, null, 2) })
  }

  const connectionTest = useMutation({
    mutationFn: (payload: { profileId?: string; draftProfile?: Record<string, unknown> }) =>
      testConnection(
        payload.profileId
          ? { profile_id: payload.profileId }
          : { draft: payload.draftProfile as never },
      ),
    onSuccess: (data, variables) => {
      setTestResult({
        target: variables.profileId ? `配置 ${variables.profileId}` : '当前表单草稿',
        data,
      })
      setError(null)
    },
    onError: (err: unknown) => {
      setTestResult(null)
      setError(describeError(err))
    },
  })

  // 参数校验在提交前完成：本地不合法就直接返回，不触发请求，也不会被 onError 覆盖提示。
  const save = useMutation({
    mutationFn: async (params: Record<string, unknown>) => {
      if (!editingId) {
        return createProfile({
          name: form.name,
          protocol: form.protocol,
          base_url: form.baseUrl,
          model: form.model,
          params,
          credential_mode: form.credentialMode,
          api_key: form.apiKey || null,
        })
      }
      return updateProfile(editingId, {
        name: form.name,
        protocol: form.protocol,
        base_url: form.baseUrl,
        model: form.model,
        params,
        api_key: keyAction === 'replace' && form.apiKey ? form.apiKey : null,
        remove_api_key: keyAction === 'remove',
        credential_mode: keyAction === 'replace' ? form.credentialMode : null,
      })
    },
    onSuccess: (profile) => {
      setError(null)
      setNotice(
        profile.credential_warning ??
          (profile.has_key ? `已保存配置「${profile.name}」与密钥。` : `已保存配置「${profile.name}」（未保存密钥）。`),
      )
      resetForm() // 成功后清空表单，api_key 不留在内存里
      void queryClient.invalidateQueries({ queryKey: profileKeys.profiles() })
    },
    onError: async (err: unknown) => {
      setNotice(null)
      if (err instanceof ApiError && err.code === 'VERSION_CONFLICT') {
        await queryClient.invalidateQueries({ queryKey: profileKeys.profiles() })
        setError('该配置已被其它操作更新，已刷新列表，请重新编辑后再保存。')
        return
      }
      setError(describeError(err))
    },
  })

  const remove = useMutation({
    mutationFn: (profileId: string) => deleteProfile(profileId),
    onSuccess: () => {
      setError(null)
      setNotice('配置已删除，其凭据引用也已清理。')
      resetForm()
      void queryClient.invalidateQueries({ queryKey: profileKeys.profiles() })
    },
    onError: (err: unknown) => {
      setNotice(null)
      setError(describeError(err))
    },
  })

  return (
    <div className="ndr-page ndr-settings">
      <header className="card ndr-page-header">
        <div>
          <h2>模型配置</h2>
          <p className="hint">
            管理 API 提供方、模型与密钥；密钥只在提交时发送，界面不回显。
          </p>
        </div>
      </header>
      <section className="card">
        <h2>新增或更新配置</h2>
        <p className="hint">
          在这里填写 API 根地址、模型名与密钥；不需要改源码。密钥只在提交时出现，
          保存后接口只返回 <code>has_key</code>，不会回传密钥本身。保存前可以用
          「测试当前填写内容」验证连通性，保存后也可以随时「测试连接」；除连接测试外，本页不会发起调用。
        </p>

        <form
          className="ndr-profile-form"
          onSubmit={(event) => {
            event.preventDefault()
            setNotice(null)
            setError(null)
            const params = parseParams()
            if (params === null) return
            save.mutate(params)
          }}
        >
          <label>
            名称
            <input
              type="text"
              value={form.name}
              required
              data-testid="profile-name"
              onChange={(event) => setForm({ ...form, name: event.target.value })}
            />
          </label>

          <label>
            协议
            <select
              value={form.protocol}
              data-testid="profile-protocol"
              onChange={(event) => setForm({ ...form, protocol: event.target.value })}
            >
              {(protocols.data ?? []).map((item) => (
                <option key={item.protocol} value={item.protocol}>
                  {item.protocol}
                </option>
              ))}
              {!protocols.data && <option value={form.protocol}>{form.protocol}</option>}
            </select>
          </label>

          {selectedProtocol && (
            <p className="hint" data-testid="protocol-capabilities">
              {selectedProtocol.notes} · json_object：{selectedProtocol.supports_json_object ? '支持' : '不支持'} ·
              json_schema：{selectedProtocol.supports_json_schema ? '支持' : '未声明'} · 需要 Key：
              {selectedProtocol.requires_api_key ? '是' : '否'}
            </p>
          )}

          <label>
            Base URL（API 根路径）
            <input
              type="text"
              value={form.baseUrl}
              required
              placeholder="https://api.example.com/v1"
              data-testid="profile-base-url"
              onChange={(event) => setForm({ ...form, baseUrl: event.target.value })}
            />
          </label>

          <label>
            模型名
            <input
              type="text"
              value={form.model}
              required
              placeholder="example-chat-model"
              data-testid="profile-model"
              onChange={(event) => setForm({ ...form, model: event.target.value })}
            />
          </label>

          <fieldset>
            <legend>常用生成参数</legend>
            <p className="hint">留空表示沿用提供方或任务默认值。输出上限是每次调用的上限，不是本次任务的总额度。
              思考模式和强度请在「预览与处理」里调整。</p>
            {([
              ['max_tokens', '每次调用最大输出 Token', 1, undefined, 1],
              ['temperature', '随机性（越低越稳定）', 0, 2, 0.1],
              ['top_p', '采样范围', 0, 1, 0.05],
              ['timeout_seconds', '请求超时（秒）', 1, undefined, 1],
            ] as const).map(([key, label, min, max, step]) => <label className="ndr-field" key={key}>
              {label}
              <input type="number" data-testid={`profile-${key}`} min={min} max={max} step={step}
                value={(JSON.parse(form.paramsText)[key] as number | undefined) ?? ''}
                onChange={event => changeParameter(key, event.target.value)} />
            </label>)}
          </fieldset>
          <details>
            <summary>查看生成参数（只读）</summary>
            <p className="hint">保留已有提供方专用参数；本页只调整上面的常用项，不会删除其它参数。</p>
            <textarea value={form.paramsText} rows={5} readOnly data-testid="profile-params" aria-label="生成参数（只读）" />
          </details>

          <fieldset>
            <legend>凭据</legend>
            {editingId && (
              <div className="ndr-radio-row">
                {(
                  [
                    ['keep', '保持不变'],
                    ['replace', '替换密钥'],
                    ['remove', '清除密钥'],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value}>
                    <input
                      type="radio"
                      name="key-action"
                      value={value}
                      checked={keyAction === value}
                      onChange={() => setKeyAction(value)}
                      data-testid={`key-action-${value}`}
                    />
                    {label}
                  </label>
                ))}
              </div>
            )}
            {(!editingId || keyAction === 'replace') && (
              <>
                <div className="ndr-radio-row">
                  {(
                    [
                      ['session', '仅本会话'],
                      ['system', '系统凭据库'],
                      ['none', '不需要密钥（本地服务）'],
                    ] as const
                  ).map(([value, label]) => (
                    <label key={value}>
                      <input
                        type="radio"
                        name="credential-mode"
                        value={value}
                        checked={form.credentialMode === value}
                        onChange={() => setForm({ ...form, credentialMode: value })}
                        data-testid={`credential-mode-${value}`}
                      />
                      {label}
                    </label>
                  ))}
                </div>
                <label>
                  API Key（留空表示不保存密钥）
                  <input
                    type="password"
                    value={form.apiKey}
                    autoComplete="off"
                    data-testid="profile-api-key"
                    onChange={(event) => setForm({ ...form, apiKey: event.target.value })}
                  />
                </label>
              </>
            )}
          </fieldset>

          <div className="ndr-form-actions">
            <button type="submit" className="ndr-primary" data-testid="profile-save" disabled={save.isPending}>
              {editingId ? '保存修改' : '新建配置'}
            </button>
            <button
              type="button"
              data-testid="profile-test-draft"
              disabled={connectionTest.isPending}
              onClick={() => {
                const params = parseParams()
                if (params === null) return
                connectionTest.mutate({
                  draftProfile: {
                    name: form.name || '草稿',
                    protocol: form.protocol,
                    base_url: form.baseUrl,
                    model: form.model,
                    params,
                    credential_mode: form.credentialMode,
                    api_key: form.apiKey || null,
                  },
                })
              }}
            >
              {connectionTest.isPending ? '正在测试…' : '测试当前填写内容'}
            </button>
            {editingId && (
              <button type="button" onClick={resetForm} data-testid="profile-cancel">
                取消编辑
              </button>
            )}
          </div>
        </form>

        {testResult && (
          <div className="ndr-connection-result" data-testid="connection-result">
            <p className={testResult.data.ok ? 'status-ok' : 'status-error'}>
              {testResult.target}：{testResult.data.ok ? '连接成功' : '连接失败'}
              {testResult.data.latency_ms !== null ? `（${testResult.data.latency_ms} ms）` : ''}
            </p>
            <ul className="hint">
              <li>适配器：{testResult.data.adapter}</li>
              <li>
                协议 / 模型：{testResult.data.protocol} / {testResult.data.model}
              </li>
              <li>
                用量：
                {testResult.data.usage_unknown
                  ? '未知（提供方未返回 usage，不按 0 计）'
                  : `输入 ${testResult.data.usage?.input_tokens ?? '?'} · 输出 ${
                      testResult.data.usage?.output_tokens ?? '?'
                    } · 合计 ${testResult.data.usage?.total_tokens ?? '?'}`}
              </li>
              {testResult.data.error_code && <li>错误码：{testResult.data.error_code}</li>}
              <li>{testResult.data.detail}</li>
            </ul>
            {testResult.data.adapter === 'fake-provider' && (
              <p className="status-error" data-testid="fake-provider-warning">
                这是测试用适配器：没有访问任何真实服务，结果只验证界面流程，不代表模型可用。
              </p>
            )}
            {testResult.data.ok && testResult.data.adapter !== 'fake-provider' && (
              <p className="hint">连接成功只说明鉴权与 JSON 输出可解析，未评估小说标注效果。</p>
            )}
          </div>
        )}

        {notice && (
          <p className="status-ok" data-testid="settings-notice">
            {notice}
          </p>
        )}
        {error && (
          <p className="status-error" data-testid="settings-error">
            {error}
          </p>
        )}
      </section>

      <section className="card">
        <h2>已保存的配置</h2>
        {profiles.isPending && <p className="hint">正在读取配置…</p>}
        {profiles.isError && <p className="status-error">配置读取失败，请确认后端已启动。</p>}
        {profiles.isSuccess && profiles.data.length === 0 && (
          <p className="hint" data-testid="profiles-empty">
            还没有模型配置。填写上方表单即可保存，不需要修改源码。
          </p>
        )}
        <div className="ndr-profile-list">
          {(profiles.data ?? []).map((profile) => (
            <article className="ndr-profile-card" key={profile.id} data-testid="profile-card">
              <header>
                <h3>{profile.name}</h3>
                <span className={profile.has_key ? 'ndr-badge ndr-badge-key' : 'ndr-badge'}>
                  {profile.has_key ? '已保存密钥' : '未保存密钥'}
                </span>
              </header>
              <dl>
                <dt>协议</dt>
                <dd>{profile.protocol}</dd>
                <dt>Base URL</dt>
                <dd>{profile.base_url}</dd>
                <dt>模型</dt>
                <dd>{profile.model}</dd>
                <dt>凭据</dt>
                <dd>{profile.credential_mode}</dd>
                <dt>版本</dt>
                <dd>{profile.version}</dd>
              </dl>
              <div className="ndr-form-actions">
                <button
                  type="button"
                  data-testid={`profile-edit-${profile.id}`}
                  onClick={() => {
                    setEditingId(profile.id)
                    setForm(formFromProfile(profile))
                    setKeyAction('keep')
                    setNotice(null)
                    setError(null)
                  }}
                >
                  编辑
                </button>
                <button
                  type="button"
                  disabled={connectionTest.isPending}
                  onClick={() => connectionTest.mutate({ profileId: profile.id })}
                  data-testid={`profile-test-${profile.id}`}
                >
                  测试连接
                </button>
                <button
                  type="button"
                  className="ndr-danger"
                  onClick={() => remove.mutate(profile.id)}
                  data-testid={`profile-delete-${profile.id}`}
                >
                  删除
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  )
}
