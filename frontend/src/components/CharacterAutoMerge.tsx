import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { fetchLatestCharacterAutoMerge, confirmCharacterAutoMerge } from '../api/characters'
import { enqueueWork, useAdmissions, stopAdmission } from '../processing/workQueue'
import { Link } from 'react-router-dom'
import { freshIdempotencyKey, pauseJob } from '../api/jobs'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { useProcessingPreferences, inferenceOptions } from '../processing/preferences'
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'
import { ThinkingSettings } from './ThinkingSettings'
import { JOB_STATE_LABELS, JobPanel } from './JobPanel'
import { OperationTimer } from './OperationTimer'
import { CollapsibleBlock } from './CollapsibleBlock'
import { PaginatedItems } from './ListPagination'

export function CharacterAutoMerge({ bookId, versionId, count, disabled, onBusyChange, onSaved, visibleFromCp }: {
  bookId: string; versionId?: string | null; count: number; disabled: boolean
  onBusyChange: (value: boolean) => void; onSaved: () => Promise<void>
  visibleFromCp?: number | null
}) {
  const [open, setOpen] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [waitingDetails, setWaitingDetails] = useState(false)
  const [preferences, update] = useProcessingPreferences()
  const refreshed = useRef('')
  const admissions = useAdmissions()
  const queued = admissions.find(item => item.bookId === bookId && item.versionId === versionId && item.payload.type === 'merge' && ['queued', 'running'].includes(item.phase))
  const queueError = [...admissions].reverse().find(item => item.bookId === bookId && item.versionId === versionId && item.payload.type === 'merge')?.error
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal), enabled: open })
  const result = useQuery({ queryKey: ['character-auto-merge', bookId, versionId],
    queryFn: ({ signal }) => fetchLatestCharacterAutoMerge(bookId, versionId!, signal), enabled: Boolean(versionId),
    refetchInterval: query => queued || query.state.data && !query.state.error && !TERMINAL_JOB_STATES.has(query.state.data.state) ? 2000 : false })
  const jobId = result.data?.job_id
  const waitingForNewJob = Boolean(queued && (!queued.jobId || queued.jobId !== jobId))
  const resultRunning = Boolean(result.data && !TERMINAL_JOB_STATES.has(result.data.state))
  useEffect(() => { if (jobId) setOpen(true); setSelected([]); setConfirmed(false) }, [jobId])
  const start = useMutation({ mutationFn: () => enqueueWork({ bookId, versionId: versionId!, title: '全书人物自动合并', keys: ['merge'], payload: { type: 'merge', input: {
    book_version_id: versionId!, profile_id: preferences.profileId,
    inference_options: inferenceOptions(preferences), max_total_tokens: preferences.tokenLimit,
    idempotency_key: freshIdempotencyKey('character-auto-merge', `${bookId}:${versionId}`), run_now: true,
  } } }), onSuccess: () => { setConfirmed(false) }, onSettled: async () => { await result.refetch() } })
  const stop = useMutation({ mutationFn: () => pauseJob(jobId!), onSuccess: () => { void result.refetch() } })
  const stopQueued = useMutation({ mutationFn: (id: string) => stopAdmission(id) })
  const accept = useMutation({ mutationFn: (ids: string[]) => visibleFromCp == null
    ? confirmCharacterAutoMerge(bookId, jobId!, ids)
    : confirmCharacterAutoMerge(bookId, jobId!, ids, visibleFromCp),
    onSuccess: async () => { await result.refetch() } })
  const noSuggestions = result.data?.state === 'COMPLETED' && (
    result.data.phase === 'no_suggestions' || result.data.phase === 'awaiting_confirmation'
      && Array.isArray(result.data.proposals) && result.data.proposals.length === 0
  )
  const awaiting = result.data?.phase === 'awaiting_confirmation' && !noSuggestions
  const busy = Boolean(queued) || start.isPending || accept.isPending || Boolean(versionId && result.isPending) || Boolean(result.data && !TERMINAL_JOB_STATES.has(result.data.state))
  useEffect(() => { onBusyChange(busy); return () => onBusyChange(false) }, [busy, onBusyChange])
  useEffect(() => {
    if (result.data?.state === 'COMPLETED' && !noSuggestions && result.data.phase !== 'awaiting_confirmation' && result.data.phase !== 'discarded' && refreshed.current !== result.data.job_id) {
      refreshed.current = result.data.job_id
      void onSaved()
    }
  }, [result.data, onSaved, noSuggestions])
  const profileReady = profiles.data?.some(profile => profile.id === preferences.profileId)
  const blocked = busy || result.isError || awaiting
  const blockedReason = result.isError ? '任务状态读取失败，重新读取成功后才能开始新的分析。'
    : queued ? '本书自动合并任务已在任务队列中，请等待完成或前往队列停止。'
    : start.isPending ? '正在提交自动合并任务，请勿重复启动。'
    : accept.isPending ? '正在保存本次合并决定，请稍候。'
    : versionId && result.isPending ? '正在读取已有任务，确认状态后才能开始新的分析。'
    : result.data && !TERMINAL_JOB_STATES.has(result.data.state)
      ? result.data.state === 'PAUSING' ? '自动合并正在停止收尾，结束后才能重新配置。'
        : '自动合并任务正在排队或运行，结束后才能重新配置。'
    : awaiting ? '已有合并建议等待确认，请先确认或放弃当前建议，再开始新的分析。' : null
  const proposals = result.data?.proposals ?? []
  const duplicateCount = proposals.reduce((sum, group) => sum + group.sources.length, 0)
  const missingDescription = proposals.some(group => selected.includes(group.target.character_id) && !group.merged_description?.trim())
  return <section className="card ndr-character-merge" aria-label="自动合并人物">
    <label className="ndr-field"><span><input type="checkbox" checked={open} disabled={busy}
      onChange={event => setOpen(event.target.checked)} /> 自动合并人物（先预览，再确认）</span></label>
    {versionId && result.isPending && <p role="status">正在读取已有任务…</p>}
    {result.isError && <p className="status-error" role="alert">任务状态读取失败：{result.error.message} <button onClick={() => void result.refetch()}>重新读取进度</button>。确认已有任务状态前不能启动新任务。</p>}
    {open && <>
      <h3 className="ndr-step-heading"><span className="ndr-step-badge">1</span>分析重复人物与姓名</h3>
      <p className="hint">仅分析已保存的人物姓名、别名和说明，请先保存编辑。也会检查代称及已有姓名，包括人工指定的名称，提出有资料依据的修正建议。分析会消耗 Tokens，但不会修改人物。模型可能误判，完成后由你选择接受哪些建议；确认后更新已有对白与导出，不改正文或章节完成状态。</p>
      <ThinkingSettings disabled={blocked} disabledReason={blockedReason ?? undefined} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => update({ profileId })} profileTestId="character-merge-profile" />
      {profiles.isError && <p role="alert" className="status-error">模型配置读取失败：{profiles.error.message}</p>}
      <label className="ndr-field">本次 Token 使用上限（留空不限）<input type="number" min={1}
        disabled={blocked} value={preferences.tokenLimit ?? ''} onChange={event => {
          const value = event.target.value === '' ? null : Number(event.target.value)
          if (value === null || Number.isSafeInteger(value) && value > 0) update({ tokenLimit: value })
        }} /></label>
      <label className="ndr-field"><span><input type="checkbox" checked={confirmed} disabled={blocked}
        onChange={event => setConfirmed(event.target.checked)} /> 我同意调用模型生成合并建议（会消耗 Tokens）</span></label>
      <button disabled={blocked || !versionId || count < 1 || !profileReady || !confirmed}
        title={blockedReason ?? (!versionId ? '请先重新读取书籍版本。' : count < 1 ? '请先识别或添加人物。' : !profileReady ? '请先选择可用的模型配置。' : !confirmed ? '请先勾选同意调用模型。' : undefined)}
        onClick={() => start.mutate()}>分析合并建议</button>
      {disabled && <p className="hint">本书处理尚未结束，合并分析会加入队列，等待前序处理完成后执行。</p>}
      {queued && <p role="status">已添加自动合并任务。<Link to="/tasks">查看任务队列</Link> <button className="ndr-danger" disabled={queued.stopRequested || stopQueued.isPending} title={queued.stopRequested || stopQueued.isPending ? '停止请求已提交，请等待安全收尾。' : undefined} onClick={() => stopQueued.mutate(queued.id)}>停止本次分析</button></p>}
      {queueError && <p role="alert" className="status-error">{queueError}</p>}
      {count < 1 && <p className="hint">至少有一个人物才能分析合并或更名建议。</p>}
      {!profileReady && <p className="hint">请选择可用的模型配置。</p>}
      {!confirmed && !awaiting && <p className="hint">开始前请勾选同意调用模型。</p>}
      {awaiting && <p className="hint">请先确认或放弃当前建议，再开始新的分析。</p>}
      {(start.error || stop.error || accept.error) && <p className="status-error" role="alert">{(start.error ?? stop.error ?? accept.error)?.message}</p>}
      {stopQueued.error && <p className="status-error" role="alert">{stopQueued.error.message}</p>}
      {queued && waitingForNewJob && <div role="status">
        <p>本次自动合并：{queued.jobId ? '正在读取进度' : '等待执行'}</p>
        <OperationTimer key={queued.id} startedAt={queued.createdAt} />
        <p className="hint">{queued.jobId ? '任务已提交，正在读取本次进度；模型可能已开始处理。' : queued.waiting?.reason ?? '等待前面的本书处理范围或可用并发额度，尚未调用模型。'}{queued.waiting?.title && ` 等待任务：${queued.waiting.title}`}</p>
        {queued.waiting?.jobId && <button onClick={() => setWaitingDetails(value => !value)}>查看等待任务</button>}
        {waitingDetails && queued.waiting?.jobId && <JobPanel jobId={queued.waiting.jobId} />}
      </div>}
      {jobId && <div>
        {result.data && <>
          <p role="status">自动合并：{noSuggestions ? '分析完成，没有可接受的合并或更名建议' : awaiting ? '建议已生成，等待确认' : result.data.phase === 'discarded' ? '本次建议已放弃' : JOB_STATE_LABELS[result.data.state]}</p>
          {waitingForNewJob && <p className="hint">以下为上一次合并结果。</p>}
          <OperationTimer key={jobId} startedAt={Date.parse(result.data.created_at)} finishedAt={resultRunning ? null : Date.parse(result.data.updated_at)} />
          <p>已知消耗 {result.data.usage?.total_tokens ?? 0} Tokens{result.data.unknown_usage_runs > 0 ? `；另有 ${result.data.unknown_usage_runs} 次调用用量未知` : ''}</p>
          {noSuggestions && <p className="hint">本次未修改人物，无需确认或放弃。{result.data.skipped_groups > 0 ? `有 ${result.data.skipped_groups} 组建议未达到接受条件，未纳入结果。` : '模型未提供合并或更名建议。'}你可以调整配置，重新勾选同意后再次分析；不会自动调用模型。</p>}
          {result.data.last_error && <p className="status-error" role="alert">{result.data.last_error}</p>}
          {awaiting && Boolean(result.data.validation_issues?.length) && <p role="status">部分建议未通过检查，已排除；其余建议仍可查看并选择确认。</p>}
          {Boolean(result.data.validation_issues?.length) && <details>
            <summary>查看校验详情（{result.data.validation_issues?.length} 处）</summary>
            <ul className="ndr-merge-text">{result.data.validation_issues?.map((issue, index) => <li key={index}>
              {issue.message}{issue.field ? `；字段：${issue.field}` : ''}
            </li>)}</ul>
            <p className="hint">{awaiting ? '未通过检查的组不能确认；请核对下方有效建议后选择接受或放弃。' : result.data.phase === 'applied' ? '未通过检查的组没有执行，仅合并了你选择的有效建议。' : '本次没有执行合并，不会自动再次调用模型。请核对问题后决定是否重新分析。'}</p>
          </details>}
          {awaiting && <section className="ndr-merge-preview" aria-label="合并建议预览">
            <h3 className="ndr-step-heading"><span className="ndr-step-badge">2</span>预览并选择合并建议</h3>
            <p className="hint">默认不选中。请核对姓名、别名、依据和整理后的说明；确认后会用新说明替换保留人物的原说明，未选中的人物保持不变。确认后无法自动撤销，不会再次调用模型。</p>
            <p>共 {proposals.length} 组建议，涉及 {duplicateCount} 条重复人物记录；已选 {selected.length} 组。</p>
            <div className="ndr-form-actions">
              <button title={disabled || busy ? blockedReason ?? undefined : !proposals.length ? '没有可选择的合并建议。' : undefined} disabled={disabled || busy || !proposals.length} onClick={() => setSelected(proposals.map(group => group.target.character_id))}>全选合并建议</button>
              <button title={disabled || busy ? blockedReason ?? undefined : !selected.length ? '尚未选择建议，无需清空。' : undefined} disabled={disabled || busy || !selected.length} onClick={() => setSelected([])}>清空选择</button>
            </div>
            {!result.data.proposals?.length && <p>没有可接受的合并建议。</p>}
            <CollapsibleBlock title="合并建议明细" summary={`共 ${proposals.length} 组建议；已选 ${selected.length} 组`}>
            <PaginatedItems label="合并建议" pageSize={5} scope={bookId}>
            {proposals.map(group => <article className="card ndr-merge-card" key={group.target.character_id}>
              <label className="ndr-field"><span><input type="checkbox" disabled={disabled || busy} checked={selected.includes(group.target.character_id)}
                onChange={event => setSelected(ids => event.target.checked ? [...ids, group.target.character_id] : ids.filter(id => id !== group.target.character_id))} />
                接受：{group.sources.length ? group.sources.map(source => source.name).join('、') : group.target.name} → {group.preferred_name || group.target.name}</span></label>
              {group.preferred_name && <p className="ndr-merge-text">正式名称：{group.target.name} → {group.preferred_name}；原称呼保留为别名。</p>}
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
            </PaginatedItems>
            </CollapsibleBlock>
            {result.data.skipped_groups > 0 && <p>另有 {result.data.skipped_groups} 组未纳入建议，原人物保持不变。</p>}
            <div className="ndr-form-actions">
              <button title={disabled || busy ? blockedReason ?? undefined : !selected.length ? '请先选择至少一组合并建议。' : missingDescription ? '所选旧建议缺少人物说明，请放弃后重新分析。' : undefined} className="ndr-primary" disabled={disabled || busy || selected.length === 0 || missingDescription} onClick={() => accept.mutate(selected)}>确认合并所选 {selected.length} 组</button>
              <button title={disabled || busy ? blockedReason ?? undefined : undefined} disabled={disabled || busy} onClick={() => accept.mutate([])}>放弃本次建议</button>
            </div>
            {selected.length === 0 && <p className="hint">请至少选择一组建议后确认合并，也可以放弃本次结果。</p>}
          </section>}
          {result.data.state === 'COMPLETED' && !noSuggestions && !awaiting && result.data.phase !== 'discarded' && <>
            <p role="status">合并了 {result.data.merged_count} 个重复人物{result.data.merges?.some(group => group.previous_name) ? `；更新了 ${result.data.merges.filter(group => group.previous_name).length} 个正式名称` : ''}{result.data.skipped_groups ? `；保留 ${result.data.skipped_groups} 组未合并` : ''}。</p>
            <CollapsibleBlock title="合并结果明细" summary={`共 ${result.data.merges?.length ?? 0} 项`}>
            <PaginatedItems label="合并结果" listTag="ul" className="ndr-merge-text">{(result.data.merges ?? []).map(group => <li key={group.target_character_id}>
              {group.source_names.join('、') || group.previous_name} → {group.target_name}：{group.reason}
            </li>)}</PaginatedItems>
            </CollapsibleBlock>
          </>}
          {busy && !queued && <button title={stop.isPending || result.data.state === 'PAUSING' ? '停止请求已提交，请等待模型调用安全收尾。' : undefined} className="ndr-danger" disabled={stop.isPending || result.data.state === 'PAUSING'} onClick={() => stop.mutate()}>停止自动合并</button>}
        </>}
      </div>}
    </>}
  </section>
}
