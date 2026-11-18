import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { OperationTimer, useRequestClock } from '../components/OperationTimer'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

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
import { credentialLabel, protocolLabel } from '../ui/labels'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { PaginatedItems } from '../components/ListPagination'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import '../styles/models.css'

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
  const { hash } = useLocation()
  const [view, setView] = useState<'editor' | 'saved'>(() => hash === '#saved-profiles' ? 'saved' : 'editor')
  const editorRef = useRef<HTMLElement>(null)
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
  const protocolHelp = form.protocol === 'chat-completions-compatible'
    ? '适用于使用 OpenAI 接口格式的云端模型和本地服务。服务地址填写 API 根地址，系统会追加 /chat/completions；模型名称按服务商提供的信息填写。云端服务通常需要 API 密钥，在凭据中选择保存方式并填写密钥。明确无需认证的本地服务可选择“无需密钥（No API Key）”。'
    : form.protocol === 'anthropic-messages'
      ? '适用于 Anthropic Messages 接口。官方服务地址可填 https://api.anthropic.com/v1，系统会追加 /messages；兼容服务按服务商说明填写 API 根地址和模型名称。官方服务需要 API 密钥。思考模式按模型支持情况选择；开启思考时随机性与采样使用服务默认值。手动思考要求最大输出 Token 大于 1024，自适应使用所选思考强度。'
      : form.protocol === 'fake-provider'
        ? '用于本地模拟界面流程，返回测试数据。凭据选择“无需密钥（No API Key）”。'
        : `${selectedProtocol?.notes ?? ''} 服务地址、模型名称和是否需要 API 密钥，请按服务商提供的接入说明填写。`
  const baseUrlHelp = form.protocol === 'anthropic-messages'
    ? '填写 API 根地址，例如 https://api.anthropic.com/v1；系统会追加 /messages。'
    : form.protocol === 'chat-completions-compatible'
      ? '填写 API 根地址，例如 https://api.example.com/v1；系统会追加 /chat/completions。'
      : '填写模型服务提供的 API 根地址。'

  useEffect(() => {
    if (view === 'editor' && editingId) editorRef.current?.focus({ preventScroll: true })
  }, [view, editingId])

  useEffect(() => {
    if (hash === '#saved-profiles') setView('saved')
    else if (['#profile-editor', '#profile-generation', '#profile-thinking', '#profile-credentials'].includes(hash)) setView('editor')
  }, [hash])

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

  function changeThinkingDefault(key: 'mode' | 'effort', value: string) {
    const params = JSON.parse(form.paramsText) as Record<string, unknown>
    if (key === 'effort') {
      if (value === 'default') delete params.reasoning_effort
      else params.reasoning_effort = value
    } else {
      const current = params.thinking
      const thinking = current && typeof current === 'object' && !Array.isArray(current)
        ? { ...(current as Record<string, unknown>) } : {}
      if (value === 'default') delete thinking.type
      else thinking.type = value
      if (Object.keys(thinking).length) params.thinking = thinking
      else delete params.thinking
      if (value === 'disabled') delete params.reasoning_effort
    }
    setForm({ ...form, paramsText: JSON.stringify(params, null, 2) })
  }

  const connectionTest = useMutation({
    mutationFn: (payload: { profileId?: string; profileName?: string; draftProfile?: Record<string, unknown> }) =>
      testConnection(
        payload.profileId
          ? { profile_id: payload.profileId }
          : { draft: payload.draftProfile as never },
      ),
    onSuccess: (data, variables) => {
      setTestResult({
        target: variables.profileId ? `配置「${variables.profileName ?? variables.profileId}」` : `填写内容「${variables.draftProfile?.name ?? '草稿'}」`,
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
      setView('saved')
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
    onSuccess: (_data, deletedId) => {
      setError(null)
      setNotice('配置已删除，其凭据引用也已清理。')
      if (editingId === deletedId) {
        resetForm()
        setView('saved')
      }
      void queryClient.invalidateQueries({ queryKey: profileKeys.profiles() })
    },
    onError: (err: unknown) => {
      setNotice(null)
      setError(describeError(err))
    },
  })

  const testClock = useRequestClock(connectionTest.isPending)
  const busy = save.isPending || remove.isPending
  const busyReason = save.isPending
    ? '正在保存模型配置，请等待保存完成。'
    : remove.isPending ? '正在删除模型配置，请等待删除完成。' : undefined
  const testDisabledReason = busyReason ?? (connectionTest.isPending ? '连接测试正在进行，请等待本次测试结束。' : undefined)
  const params = JSON.parse(form.paramsText)
  const thinkingDisabled = params.thinking?.type === 'disabled'

  return (
    <div className="ndr-page ndr-model-settings">
      <header className="card ndr-page-header">
        <div>
          <h2>模型配置</h2>
          <p className="hint">连接模型服务，管理账号与生成参数。</p>
        </div>
      </header>
      <div className="ndr-page-navigation">
        <nav className="ndr-section-nav ndr-model-views" aria-label="模型配置视图">
          <button type="button" data-testid="model-view-editor" aria-pressed={view === 'editor'}
            onClick={() => setView('editor')}>{editingId ? '编辑配置' : '新建配置'}</button>
          <button type="button" data-testid="model-view-saved" aria-pressed={view === 'saved'}
            onClick={() => setView('saved')}>查看配置</button>
        </nav>
      </div>

      {(busy || testClock.startedAt > 0 || testResult || notice || error) && (
        <section className="ndr-model-feedback" aria-label="配置操作反馈">
          <OperationTimer {...testClock} />
          {busyReason && <p role="status" className="hint">{busyReason}</p>}
          {notice && <p className="status-ok" role="status" data-testid="settings-notice">{notice}</p>}
          {error && <p className="status-error" role="alert" data-testid="settings-error">{error}</p>}
          {testResult && (
            <div className="ndr-connection-result" data-testid="connection-result">
              <p className={testResult.data.ok ? 'status-ok' : 'status-error'}>
                {testResult.target}：{testResult.data.ok ? '连接成功' : '连接失败'}
                {testResult.data.latency_ms !== null ? '（' + testResult.data.latency_ms + ' ms）' : ''}
              </p>
              <p className="hint">{testResult.data.detail}</p>
              {testResult.data.adapter === 'fake-provider' && (
                <p className="status-error" data-testid="fake-provider-warning">
                  本地模拟测试完成，结果用于验证界面流程。真实模型连接请选择对应的服务协议后测试。
                </p>
              )}
              {testResult.data.ok && testResult.data.adapter !== 'fake-provider' && (
                <p className="hint">鉴权与 JSON 输出校验通过。小说标注效果请在“预览与处理”中试运行后查看。</p>
              )}
              <details>
                <summary>连接详情</summary>
                <ul className="hint">
                  <li>适配器：{testResult.data.adapter}</li>
                  <li>协议 / 模型：{protocolLabel(testResult.data.protocol)} / {testResult.data.model}</li>
                  <li>用量：{testResult.data.usage_unknown
                    ? '未知（模型服务未返回用量）'
                    : '输入 ' + (testResult.data.usage?.input_tokens ?? '?') + ' · 输出 ' +
                      (testResult.data.usage?.output_tokens ?? '?') + ' · 合计 ' +
                      (testResult.data.usage?.total_tokens ?? '?')}</li>
                  {testResult.data.error_code && <li>错误码：{testResult.data.error_code}</li>}
                </ul>
              </details>
            </div>
          )}
        </section>
      )}

      <section className="card ndr-model-panel" id="profile-editor" tabIndex={-1} ref={editorRef}
        aria-label="模型配置表单" data-testid="model-editor-panel" hidden={view !== 'editor'}>
        <form className="ndr-model-form" onSubmit={(event) => {
          event.preventDefault()
          if (busy) return
          setNotice(null)
          setError(null)
          const parsed = parseParams()
          if (parsed !== null) save.mutate(parsed)
        }}>
          <fieldset className="ndr-model-editable" disabled={busy} title={busyReason}>
            <div className="ndr-model-columns">
              <div className="ndr-model-column">
                <fieldset className="ndr-model-group">
                  <legend>接口信息 <span className="hint">* 为必填项</span></legend>
                  <div className="ndr-model-fields">
                    <label className="ndr-field">* 配置名称（Profile Name）
                      <input type="text" value={form.name} required data-testid="profile-name"
                        title="为这组模型服务配置填写便于识别的名称。"
                        onChange={(event) => setForm({ ...form, name: event.target.value })} />
                    </label>
                    <label className="ndr-field">* 协议（API Protocol）
                      <select value={form.protocol} data-testid="profile-protocol" title={protocolHelp}
                        onChange={(event) => setForm({ ...form, protocol: event.target.value })}>
                        {(protocols.data ?? []).map((item) => (
                          <option key={item.protocol} value={item.protocol}>{protocolLabel(item.protocol)}</option>
                        ))}
                        {!protocols.data && <option value={form.protocol}>{protocolLabel(form.protocol)}</option>}
                      </select>
                    </label>
                    <label className="ndr-field">* 服务地址（Base URL）
                      <input type="text" value={form.baseUrl} required placeholder="https://api.example.com/v1"
                        data-testid="profile-base-url" title={baseUrlHelp}
                        onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} />
                    </label>
                    <label className="ndr-field">* 模型名（Model ID）
                      <input type="text" value={form.model} required placeholder="example-chat-model"
                        data-testid="profile-model" title="填写服务商提供的模型标识，大小写及版本后缀按接入说明填写。"
                        onChange={(event) => setForm({ ...form, model: event.target.value })} />
                    </label>
                  </div>
                </fieldset>

                <fieldset className="ndr-model-group">
                  <legend>密钥与凭据</legend>
                  {editingId && (
                    <div className="ndr-radio-row ndr-model-key-action">
                      {([['keep', '保持不变'], ['replace', '替换密钥'], ['remove', '清除密钥']] as const).map(([value, label]) => (
                        <label key={value}>
                          <input type="radio" name="key-action" value={value} checked={keyAction === value}
                            onChange={() => setKeyAction(value)} data-testid={'key-action-' + value} />
                          {label}
                        </label>
                      ))}
                    </div>
                  )}
                  {(!editingId || keyAction === 'replace') && (
                    <>
                      <div className="ndr-radio-row ndr-model-credentials" role="group" aria-label="密钥保存方式">
                        {([
                          ['session', '密钥用于当前运行会话，重新启动后可再次填写。'],
                          ['system', '密钥交由系统凭据库保存，配置列表会显示保存结果。'],
                          ['none', '适用于明确允许无需认证访问的本地模型服务。'],
                        ] as const).map(([value, help]) => (
                          <label key={value} title={help}>
                            <input type="radio" name="credential-mode" value={value}
                              checked={form.credentialMode === value} onChange={() => setForm({ ...form, credentialMode: value })}
                              data-testid={'credential-mode-' + value} title={help} />
                            {credentialLabel(value)}
                          </label>
                        ))}
                      </div>
                      <label className="ndr-field">接口密钥（API Key，可选）
                        <input type="password" value={form.apiKey} autoComplete="off" data-testid="profile-api-key"
                          title="填写服务商提供的 API 密钥。保存配置时按所选方式保存；草稿连接测试使用此处填写的密钥。"
                          onChange={(event) => setForm({ ...form, apiKey: event.target.value })} />
                      </label>
                    </>
                  )}
                </fieldset>
              </div>

              <div className="ndr-model-column">
                <fieldset className="ndr-model-group">
                  <legend>生成参数</legend>
                  <div className="ndr-model-fields">
                    {([
                      ['max_tokens', '输出上限（Max Tokens）', 1, undefined, 1, '限制每次模型调用的输出 Token 数；任务总额度在处理页设置。'],
                      ['temperature', '随机性（Temperature）', 0, 2, 0.1, '取值 0 至 2，数值越低，输出通常越稳定。Anthropic 开启思考时使用服务默认值。'],
                      ['top_p', '采样范围（Top P）', 0, 1, 0.05, '取值 0 至 1，用于控制候选内容的采样范围。'],
                      ['timeout_seconds', '请求超时（Timeout，秒）', 1, undefined, 1, '单次模型请求的等待时限，填写大于或等于 1 的整数秒。'],
                    ] as const).map(([key, label, min, max, step, help]) => (
                      <label className="ndr-field" key={key}>{label}
                        <input type="number" data-testid={'profile-' + key} min={min} max={max} step={step}
                          title={help + '留空沿用提供方或任务默认值。'} value={(params[key] as number | undefined) ?? ''}
                          onChange={(event) => changeParameter(key, event.target.value)} />
                      </label>
                    ))}
                  </div>
                </fieldset>

                <fieldset className="ndr-model-group">
                  <legend>默认思考设置</legend>
                  <div className="ndr-model-fields">
                    <label className="ndr-field">思考模式（Thinking Mode）
                      <select data-testid="profile-thinking-mode" value={params.thinking?.type ?? 'default'}
                        title="处理页选择“沿用模型配置”时使用这里保存的默认值；选择“模型服务默认”由服务商决定。思考功能需模型服务支持，开启后可能增加用量与等待时间；关闭思考会清除默认强度。"
                        onChange={(event) => changeThinkingDefault('mode', event.target.value)}>
                        <option value="default">模型服务默认（Default）</option>
                        <option value="disabled">关闭（Disabled）</option><option value="enabled">开启（Enabled）</option><option value="adaptive">自适应（Adaptive）</option>
                      </select>
                    </label>
                    <label className="ndr-field">思考强度（Reasoning Effort）
                      <select data-testid="profile-thinking-effort" value={params.reasoning_effort ?? 'default'} disabled={thinkingDisabled}
                        title={thinkingDisabled ? '默认思考已关闭，请先选择开启或自适应，再设置默认强度。' : '模型服务支持思考强度时使用所选值；处理页可按任务覆盖。'}
                        onChange={(event) => changeThinkingDefault('effort', event.target.value)}>
                        <option value="default">模型服务默认（Default）</option>
                        <option value="low">低（Low）</option><option value="medium">中（Medium）</option><option value="high">高（High）</option>
                      </select>
                    </label>
                  </div>
                  {thinkingDisabled && <p className="hint ndr-model-thinking-hint">默认思考已关闭；先选择开启或自适应，再调整强度。</p>}
                </fieldset>
              </div>
            </div>
          </fieldset>

          <details className="ndr-model-parameters">
            <summary>查看生成参数（只读）</summary>
            <p className="hint">完整参数包含上方常用项和已保存的提供方专用参数。</p>
            <textarea value={form.paramsText} rows={5} readOnly data-testid="profile-params" aria-label="生成参数（只读）" />
          </details>

          <footer className="ndr-model-form-footer">
            <div className="ndr-form-actions">
              <button title={busyReason} type="submit" className="ndr-primary" data-testid="profile-save" disabled={busy}>
                {editingId ? '保存修改' : '保存配置'}
              </button>
              <button type="button" data-testid="profile-test-draft" title={testDisabledReason}
                disabled={busy || connectionTest.isPending} onClick={() => {
                  const parsed = parseParams()
                  if (parsed === null) return
                  connectionTest.mutate({
                    draftProfile: {
                      name: form.name || '草稿', protocol: form.protocol, base_url: form.baseUrl, model: form.model,
                      params: parsed, credential_mode: form.credentialMode, api_key: form.apiKey || null,
                    },
                  })
                }}>{connectionTest.isPending ? '正在测试…' : '测试当前填写内容'}</button>
              {editingId && <button type="button" disabled={busy} title={busyReason} data-testid="profile-cancel"
                onClick={() => { resetForm(); setView('saved') }}>取消编辑</button>}
            </div>
            <p className="hint">连接测试会请求模型服务，可能产生费用。</p>
            {editingId && <p className="hint ndr-model-draft-key-hint">草稿测试使用当前输入的密钥；已保存密钥可在“查看配置”中测试。</p>}
            {connectionTest.isPending && <p className="hint ndr-model-draft-key-hint">连接测试正在进行，请等待本次测试结束。</p>}
          </footer>
        </form>
      </section>

      <section className="card ndr-model-panel" id="saved-profiles" aria-label="已保存的模型配置"
        data-testid="model-saved-panel" hidden={view !== 'saved'}>
        <p className="hint ndr-model-list-hint">连接测试会请求模型服务，可能产生费用。</p>
        {profiles.isPending && <p className="hint">正在读取配置…</p>}
        {profiles.isError && <ReadErrorNotice label="模型配置读取失败" error={profiles.error}
          retrying={profiles.isFetching} onRetry={() => void profiles.refetch()} />}
        {profiles.isSuccess && profiles.data.length === 0 && (
          <p className="hint" data-testid="profiles-empty">还没有模型配置。在“新建配置”中填写接口信息并保存，即可用于人物识别与对白处理。</p>
        )}
        <CollapsibleBlock title="模型配置列表" summary={'共 ' + (profiles.data?.length ?? 0) + ' 个配置'}>
          {connectionTest.isPending && <p className="hint">连接测试正在进行，请等待本次测试结束。</p>}
          <PaginatedItems label="模型配置" pageSize={5} className="ndr-profile-list">
            {(profiles.data ?? []).map((profile) => (
              <article className="ndr-profile-card" key={profile.id} data-testid="profile-card">
                <header>
                  <h3>{profile.name}</h3>
                  <span className={profile.has_key ? 'ndr-badge ndr-badge-key' : 'ndr-badge'}>
                    {profile.has_key ? '已保存密钥' : '未保存密钥'}
                  </span>
                </header>
                <dl>
                  <dt>协议（API Protocol）</dt><dd title={profile.protocol}>{protocolLabel(profile.protocol)}</dd>
                  <dt>服务地址（Base URL）</dt><dd>{profile.base_url}</dd>
                  <dt>模型名（Model ID）</dt><dd>{profile.model}</dd>
                  <dt>凭据（Credentials）</dt><dd>{credentialLabel(profile.credential_mode)}</dd>
                  <dt>版本</dt><dd>{profile.version}</dd>
                </dl>
                <div className="ndr-form-actions">
                  <button type="button" data-testid={'profile-edit-' + profile.id} disabled={busy} title={busyReason}
                    onClick={() => {
                      setEditingId(profile.id)
                      setForm(formFromProfile(profile))
                      setKeyAction('keep')
                      setNotice(null)
                      setError(null)
                      setView('editor')
                    }}>编辑</button>
                  <button type="button" disabled={busy || connectionTest.isPending} title={testDisabledReason}
                    data-testid={'profile-test-' + profile.id}
                    onClick={() => connectionTest.mutate({ profileId: profile.id, profileName: profile.name })}>测试连接</button>
                  <button type="button" className="ndr-danger" disabled={busy} title={busyReason}
                    data-testid={'profile-delete-' + profile.id} onClick={() => remove.mutate(profile.id)}>删除</button>
                </div>
              </article>
            ))}
          </PaginatedItems>
        </CollapsibleBlock>
      </section>
    </div>
  )
}
