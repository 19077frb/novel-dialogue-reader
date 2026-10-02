import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  applicationSettingsKey, fetchApplicationSettings, saveApplicationSettings, saveAndRestartApplication,
} from '../api/applicationSettings'
import type { ApplicationSettingsPatch } from '../api/applicationSettings'
import { CollapsibleBlock } from './CollapsibleBlock'

export function ApplicationSettings() {
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
  const changes = Object.fromEntries(Object.entries(draft).filter(([key, value]) =>
    JSON.stringify(value) !== JSON.stringify(data?.fields.find(field => field.key === key)?.value),
  ))
  const changed = Object.keys(changes).length > 0
  const edit = (key: string, value: ApplicationSettingsPatch['values'][string]) => {
    setDraft(previous => ({ ...previous, [key]: value }))
    setSaved(false)
    mutation.reset()
  }
  const groups = [...new Set(data?.fields.map(field => field.group) ?? [])]
  const busy = mutation.isPending || restarting
  const hostname = data?.fields.find(field => field.key === 'host')?.value === '::1' ? '[::1]' : '127.0.0.1'
  const restartAddress = `http://${hostname}:${String(data?.fields.find(field => field.key === 'port')?.value)}`
  const optionLabels: Record<string, string> = {
    system: '系统凭据库（推荐）', session: '仅本次会话', unknown: '全部标为待确认',
    deterministic: '确定性测试标注', rate_limited_once: '模拟一次限流', unavailable_once: '模拟一次服务不可用',
    timeout_once: '模拟一次超时', auth_failed_once: '模拟一次凭据错误',
  }
  return <section className="card ndr-application-settings" aria-label="应用配置">
    <h3>应用配置</h3>
    <p className="hint">无需创建 .env 文件。这里的配置保存在本机，重启后生效。普通保存不会重启，也不改变正在运行的任务；可以手动重启，或在任务结束后选择“保存并重启”。模型密钥仍在“模型配置”中管理。</p>
    {query.isPending && <p role="status">正在读取应用配置…</p>}
    {query.isError && <p className="status-error">应用配置读取失败：{query.error.message} <button onClick={() => void query.refetch()}>重新读取应用配置</button></p>}
    {data && <>
      {(saved || data.restart_required.length > 0) && <p role="status">
        {data.restart_required.length > 0
          ? `已保存，重启后生效：${data.fields.filter(field => data.restart_required.includes(field.key)).map(field => field.label).join('、')}。`
          : '配置已保存，当前值没有变化。'}
      </p>}
      {(data.restart_required.includes('port') || data.restart_required.includes('host')) && <p className="hint">重启后的阅读地址：{restartAddress}</p>}
      {data.restart_required.includes('data_dir') && <p className="hint">现有书库没有搬迁。重启后使用新目录，若尚无书籍，书架将为空。</p>}
      {groups.map(group => <CollapsibleBlock key={group} title={group}
        defaultOpen={group !== '离线测试（高级）'}>
        <div className="ndr-range-grid ndr-application-grid">
          {data.fields.filter(field => field.group === group).map(field => {
            const value = Object.hasOwn(draft, field.key) ? draft[field.key] : field.value
            const reason = busy ? '正在保存或重启，请等待完成后再修改。' : field.locked_reason
            const disabled = Boolean(reason)
            const hintId = `application-${field.key}-hint`
            const attributes = { disabled, title: reason ?? undefined, 'aria-describedby': hintId }
            return <div key={field.key}>
              <label className="ndr-field">{field.label}
                {field.kind === 'boolean'
                  ? <input type="checkbox" checked={value === true} {...attributes} onChange={event => edit(field.key, event.target.checked)} />
                  : field.kind === 'select'
                    ? <select value={String(value ?? '')} {...attributes} onChange={event => edit(field.key, event.target.value)}>
                      {field.options.map(option => <option key={option} value={option}>{optionLabels[option] ?? (option || '不模拟错误')}</option>)}
                    </select>
                    : field.kind === 'lines'
                      ? <textarea rows={3} value={Array.isArray(value) ? value.join('\n') : ''} {...attributes}
                        onChange={event => edit(field.key, event.target.value.split('\n').map(line => line.trim()).filter(Boolean))} />
                      : <input type={field.kind === 'number' ? 'number' : 'text'} value={String(value ?? '')}
                        min={field.minimum ?? undefined} max={field.maximum ?? undefined}
                        step={field.key === 'llm_timeout_seconds' ? 'any' : 1} {...attributes}
                        onChange={event => edit(field.key, field.kind === 'number'
                          ? (event.target.value === '' ? null : Number(event.target.value))
                          : (field.key === 'static_dir' && !event.target.value ? null : event.target.value))} />}
              </label>
              <p className="hint" id={hintId}>{field.description}{reason && <> {reason}</>}</p>
              {data.restart_required.includes(field.key) && <p className="hint">当前生效：{Array.isArray(field.current_value) ? field.current_value.join('、') : typeof field.current_value === 'boolean' ? (field.current_value ? '开启' : '关闭') : String(field.current_value ?? '未设置')}</p>}
            </div>
          })}
        </div>
      </CollapsibleBlock>)}
      <p className="hint">配置文件：{data.config_path}。更换书库目录不会改变这个文件的位置；请勿在文件里填写模型密钥。</p>
      <button disabled={busy || data.fields.every(field => field.locked_reason)}
        title={busy ? '正在保存或重启，请等待完成。' : data.fields.every(field => field.locked_reason) ? '全部应用配置已由启动参数或安全策略锁定，无法恢复。' : '仅恢复未锁定的应用配置草稿，仍需保存和重启。'}
        onClick={() => {
          const staticField = data.fields.find(field => field.key === 'static_dir')
          const pageWarning = staticField && !staticField.locked_reason && staticField.default_value === null && (draft.static_dir ?? staticField.value)
            ? '源码版网页目录会恢复为空，重启后本服务可能不再提供页面。' : ''
          if (!window.confirm(`恢复未锁定的应用配置默认值？可能包括书库目录、端口和密钥保存方式；不会搬迁或删除书籍、密钥。${pageWarning}其他区域不变，仍需保存并重启才生效。`)) return
          setDraft(previous => ({ ...previous, ...Object.fromEntries(data.fields.filter(field => !field.locked_reason)
            .map(field => [field.key, field.default_value])) }))
          setSaved(false); mutation.reset()
        }}>恢复应用配置默认值</button>
      <p className="hint">只恢复未锁定的应用配置；阅读、人物资料更新和自动处理偏好不变。恢复后仍需保存并重启，不会自动切换书库或删除数据。</p>
      {mutation.isError && <p role="alert" className="status-error">保存失败：{mutation.error.message}</p>}
      <button className="ndr-primary" disabled={!changed || busy}
        title={mutation.isPending ? '正在保存，请等待完成。' : !changed ? '尚未修改应用配置。' : undefined}
        onClick={() => mutation.mutate({ payload: { values: changes, revision: data.revision }, restart: false })}>
        {mutation.isPending ? '正在保存…' : '保存应用配置'}
      </button>
      <button disabled={busy || Boolean(data.restart_blocked_reason)}
        title={busy ? '正在保存或重启，请等待完成。' : data.restart_blocked_reason ?? '保存应用配置并重新启动服务。'}
        onClick={() => {
          if (!window.confirm('保存应用配置并重启服务？未保存的阅读与处理设置不会自动保存。')) return
          mutation.mutate({ payload: { values: changes, revision: data.revision }, restart: true }, {
            onSuccess: () => setRestarting(true),
          })
        }}>保存并重启</button>
      {data.restart_blocked_reason && <p className="hint">{data.restart_blocked_reason}</p>}
      {restarting && <p role="status">已保存，服务正在重新启动。请稍等几秒后
        <a href={`${restartAddress}/settings/general`}>重新打开设置</a>；若无法打开，请查看启动窗口的具体错误。</p>}
      <p className="hint">{mutation.isPending ? '正在保存，请等待完成。' : !changed ? '修改配置后可保存，重启后生效。' : '有尚未保存的修改；切换页面会丢弃这些修改。'}</p>
      <button disabled={busy} title={busy ? '请等待保存或重启完成后再读取。' : '重新读取会放弃未保存的应用配置修改。'}
        onClick={() => {
          if (changed && !window.confirm('重新读取会放弃未保存的应用配置修改，是否继续？')) return
          setDraft({}); setSaved(false); mutation.reset(); void query.refetch()
        }}>重新读取应用配置</button>
    </>}
  </section>
}
