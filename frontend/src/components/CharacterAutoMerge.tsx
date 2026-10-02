import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchCharacterAutoMergeResult, startCharacterAutoMerge } from '../api/characters'
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
  const storageKey = `ndr-character-auto-merge:${bookId}`
  const [jobId, setJobId] = useState(() => sessionStorage.getItem(storageKey) ?? '')
  const [open, setOpen] = useState(Boolean(jobId))
  const [confirmed, setConfirmed] = useState(false)
  const [preferences, update] = useProcessingPreferences()
  const refreshed = useRef('')
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal), enabled: open })
  const result = useQuery({ queryKey: ['character-auto-merge', bookId, jobId],
    queryFn: ({ signal }) => fetchCharacterAutoMergeResult(bookId, jobId, signal), enabled: Boolean(jobId),
    refetchInterval: query => !query.state.data || !TERMINAL_JOB_STATES.has(query.state.data.state) ? 2000 : false })
  const start = useMutation({ mutationFn: () => startCharacterAutoMerge(bookId, {
    book_version_id: versionId!, profile_id: preferences.profileId,
    inference_options: inferenceOptions(preferences), max_total_tokens: preferences.tokenLimit,
    idempotency_key: freshIdempotencyKey('character-auto-merge', `${bookId}:${versionId}`), run_now: true,
  }), onSuccess: job => { setJobId(job.id); sessionStorage.setItem(storageKey, job.id); setConfirmed(false) } })
  const stop = useMutation({ mutationFn: () => pauseJob(jobId), onSuccess: () => { void result.refetch() } })
  const busy = start.isPending || Boolean(jobId && (!result.data || !TERMINAL_JOB_STATES.has(result.data.state)))
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => {
    if (result.data?.state === 'COMPLETED' && refreshed.current !== result.data.job_id) {
      refreshed.current = result.data.job_id
      void onSaved()
    }
  }, [result.data, onSaved])
  const profileReady = profiles.data?.some(profile => profile.id === preferences.profileId)
  const blocked = disabled || busy
  return <section className="card" aria-label="自动合并人物">
    <label className="ndr-field"><span><input type="checkbox" checked={open} disabled={busy}
      onChange={event => setOpen(event.target.checked)} /> 自动合并人物（由模型判断并执行）</span></label>
    {open && <>
      <p className="hint">仅分析已保存的全书人物姓名、别名和说明，请先保存编辑。同名、同职务或泛称不单独作为合并依据。会调用模型并消耗 Tokens，模型仍可能误判；合并影响全部已有对白与导出，不改正文或章节完成状态。</p>
      <ThinkingSettings disabled={blocked} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => update({ profileId })} profileTestId="character-merge-profile" />
      {profiles.isError && <p role="alert" className="status-error">模型配置读取失败：{profiles.error.message}</p>}
      <label className="ndr-field">本次 Token 使用上限（留空不限）<input type="number" min={1}
        disabled={blocked} value={preferences.tokenLimit ?? ''} onChange={event => {
          const value = event.target.value === '' ? null : Number(event.target.value)
          if (value === null || Number.isSafeInteger(value) && value > 0) update({ tokenLimit: value })
        }} /></label>
      <label className="ndr-field"><span><input type="checkbox" checked={confirmed} disabled={blocked}
        onChange={event => setConfirmed(event.target.checked)} /> 我同意按模型判断直接合并，已知此操作无法自动撤销</span></label>
      <button className="ndr-primary" disabled={blocked || !versionId || count < 2 || !profileReady || !confirmed}
        onClick={() => start.mutate()}>开始自动合并</button>
      {disabled && <p className="hint">请先停止本书处理任务再自动合并。</p>}
      {count < 2 && <p className="hint">至少有两个人物才能自动合并。</p>}
      {!profileReady && <p className="hint">请选择可用的模型配置。</p>}
      {!confirmed && <p className="hint">开始前请勾选确认执行合并。</p>}
      {(start.error || stop.error) && <p className="status-error" role="alert">{(start.error ?? stop.error)?.message}</p>}
      {jobId && <div>
        {result.isError && <p className="status-error" role="alert">进度读取失败：{result.error.message} <button onClick={() => void result.refetch()}>重新读取进度</button></p>}
        {result.data && <>
          <p role="status">自动合并：{JOB_STATE_LABELS[result.data.state]}</p>
          <OperationTimer startedAt={Date.parse(result.data.created_at)} finishedAt={busy ? null : Date.parse(result.data.updated_at)} />
          <p>已知消耗 {result.data.usage?.total_tokens ?? 0} Tokens{result.data.unknown_usage_runs > 0 ? `；另有 ${result.data.unknown_usage_runs} 次调用用量未知` : ''}</p>
          {result.data.last_error && <p className="status-error" role="alert">{result.data.last_error}</p>}
          {result.data.state === 'COMPLETED' && <>
            <p role="status">合并了 {result.data.merged_count} 个重复人物{result.data.skipped_groups ? `；保留 ${result.data.skipped_groups} 组（依据不足或说明合并后过长）` : ''}。</p>
            <ul>{(result.data.merges ?? []).map(group => <li key={group.target_character_id}>
              {group.source_names.join('、')} → {group.target_name}：{group.reason}
            </li>)}</ul>
          </>}
          {busy && <button className="ndr-danger" disabled={stop.isPending || result.data.state === 'PAUSING'} onClick={() => stop.mutate()}>停止自动合并</button>}
        </>}
      </div>}
    </>}
  </section>
}
