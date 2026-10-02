import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchLatestCharacterAutoMerge, startCharacterAutoMerge, confirmCharacterAutoMerge } from '../api/characters'
import { freshIdempotencyKey, pauseJob } from '../api/jobs'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { useProcessingPreferences, inferenceOptions } from '../processing/preferences'
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'
import { ThinkingSettings } from './ThinkingSettings'
import { JOB_STATE_LABELS } from './JobPanel'
import { OperationTimer } from './OperationTimer'

export function CharacterAutoMerge({ bookId, versionId, count, disabled, onBusyChange, onSaved }: {
  bookId: string; versionId?: string | null; count: number; disabled: boolean
  onBusyChange: (value: boolean) => void; onSaved: () => Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [preferences, update] = useProcessingPreferences()
  const refreshed = useRef('')
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal), enabled: open })
  const result = useQuery({ queryKey: ['character-auto-merge', bookId, versionId],
    queryFn: ({ signal }) => fetchLatestCharacterAutoMerge(bookId, versionId!, signal), enabled: Boolean(versionId),
    refetchInterval: query => query.state.data && !query.state.error && !TERMINAL_JOB_STATES.has(query.state.data.state) ? 2000 : false })
  const jobId = result.data?.job_id
  useEffect(() => { if (jobId) setOpen(true); setSelected([]) }, [jobId])
  const start = useMutation({ mutationFn: () => startCharacterAutoMerge(bookId, {
    book_version_id: versionId!, profile_id: preferences.profileId,
    inference_options: inferenceOptions(preferences), max_total_tokens: preferences.tokenLimit,
    idempotency_key: freshIdempotencyKey('character-auto-merge', `${bookId}:${versionId}`), run_now: true,
  }), onSuccess: () => { setConfirmed(false) }, onSettled: async () => { await result.refetch() } })
  const stop = useMutation({ mutationFn: () => pauseJob(jobId!), onSuccess: () => { void result.refetch() } })
  const accept = useMutation({ mutationFn: (ids: string[]) => confirmCharacterAutoMerge(bookId, jobId!, ids),
    onSuccess: async () => { await result.refetch() } })
  const awaiting = result.data?.phase === 'awaiting_confirmation'
  const busy = start.isPending || accept.isPending || Boolean(versionId && result.isPending) || Boolean(result.data && !TERMINAL_JOB_STATES.has(result.data.state))
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => {
    if (result.data?.state === 'COMPLETED' && result.data.phase !== 'awaiting_confirmation' && result.data.phase !== 'discarded' && refreshed.current !== result.data.job_id) {
      refreshed.current = result.data.job_id
      void onSaved()
    }
  }, [result.data, onSaved])
  const profileReady = profiles.data?.some(profile => profile.id === preferences.profileId)
  const blocked = disabled || busy || result.isError || awaiting
  const proposals = result.data?.proposals ?? []
  const duplicateCount = proposals.reduce((sum, group) => sum + group.sources.length, 0)
  const missingDescription = proposals.some(group => selected.includes(group.target.character_id) && !group.merged_description?.trim())
  return <section className="card ndr-character-merge" aria-label="自动合并人物">
    <label className="ndr-field"><span><input type="checkbox" checked={open} disabled={busy}
      onChange={event => setOpen(event.target.checked)} /> 自动合并人物（先预览，再确认）</span></label>
    {versionId && result.isPending && <p role="status">正在读取已有任务…</p>}
    {result.isError && <p className="status-error" role="alert">任务状态读取失败：{result.error.message} <button onClick={() => void result.refetch()}>重新读取进度</button>。确认已有任务状态前不能启动新任务。</p>}
    {open && <>
      <h3 className="ndr-step-heading"><span className="ndr-step-badge">1</span>分析重复人物</h3>
      <p className="hint">仅分析已保存的人物姓名、别名和说明，请先保存编辑。分析会消耗 Tokens，但不会修改人物。模型可能误判，完成后由你选择接受哪些建议；确认合并影响已有对白与导出，不改正文或章节完成状态。</p>
      <ThinkingSettings disabled={blocked} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => update({ profileId })} profileTestId="character-merge-profile" />
      {profiles.isError && <p role="alert" className="status-error">模型配置读取失败：{profiles.error.message}</p>}
      <label className="ndr-field">本次 Token 使用上限（留空不限）<input type="number" min={1}
        disabled={blocked} value={preferences.tokenLimit ?? ''} onChange={event => {
          const value = event.target.value === '' ? null : Number(event.target.value)
          if (value === null || Number.isSafeInteger(value) && value > 0) update({ tokenLimit: value })
        }} /></label>
      <label className="ndr-field"><span><input type="checkbox" checked={confirmed} disabled={blocked}
        onChange={event => setConfirmed(event.target.checked)} /> 我同意调用模型生成合并建议（会消耗 Tokens）</span></label>
      <button disabled={blocked || !versionId || count < 2 || !profileReady || !confirmed}
        onClick={() => start.mutate()}>分析合并建议</button>
      {disabled && <p className="hint">请先停止本书处理任务再自动合并。</p>}
      {count < 2 && <p className="hint">至少有两个人物才能自动合并。</p>}
      {!profileReady && <p className="hint">请选择可用的模型配置。</p>}
      {!confirmed && !awaiting && <p className="hint">开始前请勾选同意调用模型。</p>}
      {awaiting && <p className="hint">请先确认或放弃当前建议，再开始新的分析。</p>}
      {(start.error || stop.error || accept.error) && <p className="status-error" role="alert">{(start.error ?? stop.error ?? accept.error)?.message}</p>}
      {jobId && <div>
        {result.data && <>
          <p role="status">自动合并：{awaiting ? '建议已生成，等待确认' : result.data.phase === 'discarded' ? '本次建议已放弃' : JOB_STATE_LABELS[result.data.state]}</p>
          <OperationTimer startedAt={Date.parse(result.data.created_at)} finishedAt={busy ? null : Date.parse(result.data.updated_at)} />
          <p>已知消耗 {result.data.usage?.total_tokens ?? 0} Tokens{result.data.unknown_usage_runs > 0 ? `；另有 ${result.data.unknown_usage_runs} 次调用用量未知` : ''}</p>
          {result.data.last_error && <p className="status-error" role="alert">{result.data.last_error}</p>}
          {awaiting && <section className="ndr-merge-preview" aria-label="合并建议预览">
            <h3 className="ndr-step-heading"><span className="ndr-step-badge">2</span>预览并选择合并建议</h3>
            <p className="hint">默认不选中。请核对姓名、别名、依据和整理后的说明；确认后会用新说明替换保留人物的原说明，未选中的人物保持不变。确认后无法自动撤销，不会再次调用模型。</p>
            <p>共 {proposals.length} 组建议，涉及 {duplicateCount} 条重复人物记录；已选 {selected.length} 组。</p>
            <div className="ndr-form-actions">
              <button disabled={disabled || busy || !proposals.length} onClick={() => setSelected(proposals.map(group => group.target.character_id))}>全选合并建议</button>
              <button disabled={disabled || busy || !selected.length} onClick={() => setSelected([])}>清空选择</button>
            </div>
            {!result.data.proposals?.length && <p>没有可接受的合并建议。</p>}
            {proposals.map(group => <article className="card ndr-merge-card" key={group.target.character_id}>
              <label className="ndr-field"><span><input type="checkbox" disabled={disabled || busy} checked={selected.includes(group.target.character_id)}
                onChange={event => setSelected(ids => event.target.checked ? [...ids, group.target.character_id] : ids.filter(id => id !== group.target.character_id))} />
                接受：{group.sources.map(source => source.name).join('、')} → {group.target.name}</span></label>
              <p className="ndr-merge-text">合并依据：{group.reason}（模型置信度 {Math.round(group.confidence * 100)}%）</p>
              {group.merged_description?.trim()
                ? <div className="ndr-merge-text"><strong>合并后的人物说明</strong><p>{group.merged_description}</p></div>
                : <p className="status-error">旧建议没有整理后的人物说明，请放弃本次建议并重新分析。</p>}
              <dl className="ndr-merge-people">{[group.target, ...group.sources].map((person, index) => <div key={person.character_id}>
                <dt>{index === 0 ? '保留人物' : '并入人物'}：{person.name}</dt>
                <dd>别名：{person.aliases?.join('、') || '无'}</dd>
                <dd>说明：{person.description || '暂无说明'}</dd>
              </div>)}</dl>
            </article>)}
            {result.data.skipped_groups > 0 && <p>另有 {result.data.skipped_groups} 组未纳入建议，原人物保持不变。</p>}
            <div className="ndr-form-actions">
              <button className="ndr-primary" disabled={disabled || busy || selected.length === 0 || missingDescription} onClick={() => accept.mutate(selected)}>确认合并所选 {selected.length} 组</button>
              <button disabled={disabled || busy} onClick={() => accept.mutate([])}>放弃本次建议</button>
            </div>
            {selected.length === 0 && <p className="hint">请至少选择一组建议后确认合并，也可以放弃本次结果。</p>}
          </section>}
          {result.data.state === 'COMPLETED' && !awaiting && result.data.phase !== 'discarded' && <>
            <p role="status">合并了 {result.data.merged_count} 个重复人物{result.data.skipped_groups ? `；保留 ${result.data.skipped_groups} 组未合并` : ''}。</p>
            <ul className="ndr-merge-text">{(result.data.merges ?? []).map(group => <li key={group.target_character_id}>
              {group.source_names.join('、')} → {group.target_name}：{group.reason}
            </li>)}</ul>
          </>}
          {busy && <button className="ndr-danger" disabled={stop.isPending || result.data.state === 'PAUSING'} onClick={() => stop.mutate()}>停止自动合并</button>}
        </>}
      </div>}
    </>}
  </section>
}
