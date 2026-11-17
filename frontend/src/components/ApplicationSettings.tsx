import { forwardRef, useEffect, useImperativeHandle, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applicationSettingsKey, fetchApplicationSettings, saveApplicationSettings, saveAndRestartApplication,
} from '../api/applicationSettings'
import type { ApplicationSettingsPatch } from '../api/applicationSettings'
import { ApiError } from '../api/client'
import { CollapsibleBlock } from './CollapsibleBlock'

const hiddenSettingKeys = new Set([
  'credential_backend', 'static_dir', 'cors_origins',
  'max_import_bytes', 'max_epub_entries', 'max_epub_total_uncompressed_bytes', 'max_epub_entry_bytes', 'max_epub_spine_items',
  'allow_fake_provider', 'fake_provider_labels', 'fake_provider_script',
])
const visibleDescriptions: Record<string, string> = {
  host: '使用本机回环地址，例如 127.0.0.1 或 localhost。',
  data_dir: '重启后使用此目录。现有书籍保留在原处；请先备份，迁移书库或选择已有书库目录。',
}

export type ApplicationSettingsState = {
  changed: boolean
  busy: boolean
  ready: boolean
  restartBlockedReason: string | null
  restarting: boolean
  restartAddress: string
}
export type ApplicationSettingsHandle = { save: (restart?: boolean) => Promise<void> }

export const ApplicationSettings = forwardRef<ApplicationSettingsHandle, {
  managed?: boolean
  onStateChange?: (state: ApplicationSettingsState) => void
}>(function ApplicationSettings({ managed = false, onStateChange }, ref) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: applicationSettingsKey, queryFn: ({ signal }) => fetchApplicationSettings(signal) })
  const [draft, setDraft] = useState<ApplicationSettingsPatch['values']>({})
  const [saved, setSaved] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const mutation = useMutation({
    mutationFn: ({ payload, restart }: { payload: ApplicationSettingsPatch; restart: boolean }) =>
      restart ? saveAndRestartApplication(payload) : saveApplicationSettings(payload),
    onSuccess: data => {
      queryClient.setQueryData(applicationSettingsKey, data)
      setDraft({})
      setSaved(true)
    },
  })
  const data = query.data
  const visibleFields = (data?.fields ?? []).filter(field => !hiddenSettingKeys.has(field.key))
  const restorableFields = visibleFields.filter(field => !field.locked_reason)
  const pendingFields = visibleFields.filter(field => data?.restart_required.includes(field.key))
  const additionalRestartRequired = data?.restart_required.some(key => !visibleFields.some(field => field.key === key)) ?? false
  const changes = Object.fromEntries(visibleFields.filter(field => Object.hasOwn(draft, field.key)
    && JSON.stringify(draft[field.key]) !== JSON.stringify(field.value)).map(field => [field.key, draft[field.key]]))
  const changed = Object.keys(changes).length > 0
  const edit = (key: string, value: ApplicationSettingsPatch['values'][string]) => {
    setDraft(previous => ({ ...previous, [key]: value }))
    setSaved(false)
    mutation.reset()
  }
  const groups = [...new Set(visibleFields.map(field => field.group))]
  const busy = mutation.isPending || restarting
  const hostname = data?.fields.find(field => field.key === 'host')?.value === '::1' ? '[::1]' : '127.0.0.1'
  const restartAddress = `http://${hostname}:${String(data?.fields.find(field => field.key === 'port')?.value)}`
  const ready = Boolean(data)
  const restartBlockedReason = data?.restart_blocked_reason ?? null
  useEffect(() => {
    onStateChange?.({ changed, busy, ready, restartBlockedReason, restarting, restartAddress })
  }, [changed, busy, ready, restartBlockedReason, restarting, restartAddress, onStateChange])
  useImperativeHandle(ref, () => ({
    async save(restart = false) {
      if (!data) throw new Error('应用配置尚未读取，请重新读取后保存。')
      await mutation.mutateAsync({ payload: { values: changes, revision: data.revision }, restart })
      if (restart) setRestarting(true)
    },
  }))
  return <section className="card ndr-application-settings" id="application-settings" aria-label="应用配置">
    <h3>应用配置</h3>
    <p className="hint">保存在本机，重启服务后生效。任务结束后可选择“保存并重启”。</p>
    {query.isPending && <p role="status">正在读取应用配置…</p>}
    {query.isError && <p className="status-error">应用配置读取失败：{query.error.message} <button onClick={() => void query.refetch()}>重新读取应用配置</button></p>}
    {data && <>
      <div className="ndr-application-groups">
      {groups.map(group => <CollapsibleBlock key={group} title={group} defaultOpen>
        <div className="ndr-range-grid ndr-application-grid">
          {visibleFields.filter(field => field.group === group).map(field => {
            const value = Object.hasOwn(draft, field.key) ? draft[field.key] : field.value
            const reason = busy ? '正在保存或重启，请等待完成后再修改。' : field.locked_reason
            const disabled = Boolean(reason)
            const hintId = `application-${field.key}-hint`
            const visibleDescription = visibleDescriptions[field.key]
            const description = visibleDescription ?? field.description
            const attributes = {
              disabled,
              title: [description, reason].filter(Boolean).join(' '),
              'aria-describedby': visibleDescription || reason ? hintId : undefined,
            }
            return <div key={field.key} className={field.kind === 'lines' || field.key === 'data_dir' ? 'ndr-setting-wide' : undefined}>
              <label className="ndr-field">{field.label}
                {field.kind === 'boolean'
                  ? <input type="checkbox" checked={value === true} {...attributes} onChange={event => edit(field.key, event.target.checked)} />
                  : field.kind === 'select'
                    ? <select value={String(value ?? '')} {...attributes} onChange={event => edit(field.key, event.target.value)}>
                      {field.options.map(option => <option key={option} value={option}>{option || '未设置'}</option>)}
                    </select>
                    : field.kind === 'lines'
                      ? <textarea rows={3} value={Array.isArray(value) ? value.join('\n') : ''} {...attributes}
                        onChange={event => edit(field.key, event.target.value.split('\n').map(line => line.trim()).filter(Boolean))} />
                      : <input type={field.kind === 'number' ? 'number' : 'text'} value={String(value ?? '')}
                        min={field.minimum ?? undefined}
                        max={field.maximum ?? undefined}
                        step={field.key === 'llm_timeout_seconds' ? 'any' : 1} {...attributes}
                        onChange={event => edit(field.key, field.kind === 'number'
                          ? (event.target.value === '' ? null : Number(event.target.value))
                          : event.target.value)} />}
              </label>
              {(visibleDescription || reason) && <div id={hintId}>
                {visibleDescription && <p className="hint">{visibleDescription}</p>}
                {reason && <p className="hint">{reason}</p>}
              </div>}
              {data.restart_required.includes(field.key) && <p className="hint">当前生效：{Array.isArray(field.current_value) ? field.current_value.join('、') : typeof field.current_value === 'boolean' ? (field.current_value ? '开启' : '关闭') : String(field.current_value ?? '未设置')}</p>}
            </div>
          })}
        </div>
      </CollapsibleBlock>)}
      </div>
      <details><summary>配置文件位置</summary><p className="hint">{data.config_path}。模型密钥在“模型配置”中管理。</p></details>
      <button disabled={busy || restorableFields.length === 0}
        title={busy ? '正在保存或重启，请等待完成。' : restorableFields.length === 0 ? '当前页面没有可恢复的未锁定配置。' : '恢复当前页面未锁定的应用配置草稿，保存和重启后生效。'}
        onClick={() => {
          if (!window.confirm('恢复当前页面未锁定的应用配置默认值？可能包括书库目录或端口；现有书籍保留在原处。点击保存并重启后生效。')) return
          setDraft(previous => ({ ...previous, ...Object.fromEntries(restorableFields
            .map(field => [field.key, field.default_value])) }))
          setSaved(false); mutation.reset()
        }}>恢复应用配置默认值</button>
      {mutation.error instanceof ApiError && mutation.error.code === 'VERSION_CONFLICT' && <button disabled={busy}
        title={busy ? '请等待保存或重启完成后再读取。' : '读取最新应用配置，确认后替换本页应用配置草稿。'}
        onClick={() => {
          if (changed && !window.confirm('读取最新配置会放弃本页未保存的应用配置修改，是否继续？')) return
          setDraft({}); setSaved(false); mutation.reset(); void query.refetch()
        }}>读取最新配置</button>}
      <div>
      {!managed && <div className="ndr-settings-save-row">
      <button className="ndr-primary" disabled={!changed || busy}
        title={mutation.isPending ? '正在保存，请等待完成。' : !changed ? '尚未修改应用配置。' : undefined}
        onClick={() => mutation.mutate({ payload: { values: changes, revision: data.revision }, restart: false })}>
        {mutation.isPending ? '正在保存…' : '保存设置'}
      </button>
      <button disabled={busy || Boolean(data.restart_blocked_reason)}
        title={busy ? '正在保存或重启，请等待完成。' : data.restart_blocked_reason ?? '保存应用配置并重新启动服务。'}
        onClick={() => {
          if (!window.confirm('保存应用配置并重启服务？')) return
          mutation.mutate({ payload: { values: changes, revision: data.revision }, restart: true }, {
            onSuccess: () => setRestarting(true),
          })
        }}>保存并重启</button>
      </div>}
      <p className="hint">应用配置保存在本机，保存后重启服务生效。</p>
      {(saved || data.restart_required.length > 0) && <p role="status">
        {pendingFields.length > 0
          ? `已保存，重启后生效：${pendingFields.map(field => field.label).join('、')}。`
          : additionalRestartRequired ? '已保存的应用配置将在重启后生效。' : '应用配置已保存。'}
        {pendingFields.length > 0 && additionalRestartRequired && '其他应用配置也将在重启后生效。'}
      </p>}
      {(data.restart_required.includes('port') || data.restart_required.includes('host')) && <p className="hint">重启后的阅读地址：{restartAddress}</p>}
      {data.restart_required.includes('data_dir') && <p className="hint">现有书籍保留在原目录。重启后使用新目录，空目录会显示空书架；如需使用现有书库，请先完成迁移。</p>}
      {!managed && mutation.isError && <p role="alert" className="status-error">保存失败：{mutation.error.message}</p>}
      {data.restart_blocked_reason && <p className="hint">{data.restart_blocked_reason}</p>}
      {!managed && restarting && <p role="status">已保存，服务正在重新启动。请稍等几秒后
        <a className="ndr-button" href={`${restartAddress}/settings/general#application-settings`}>重新打开设置</a>；若无法打开，请查看启动窗口的具体错误。</p>}
      <p className="hint">{mutation.isPending ? '正在保存，请等待完成。' : !changed ? '修改配置后可保存，重启后生效。' : '有尚未保存的修改；切换页面会丢弃这些修改。'}</p>
      </div>
    </>}
  </section>
})
