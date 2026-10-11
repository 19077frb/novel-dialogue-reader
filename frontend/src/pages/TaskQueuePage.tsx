import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchTaskQueue, fetchRecentJobs } from '../api/jobs'
import { readBatchHistory } from '../processing/batchHistory'
import { JobPanel, JOB_KIND_LABELS, JOB_STATE_LABELS } from '../components/JobPanel'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { PaginatedItems } from '../components/ListPagination'
import { FilterableTaskProgressTable, jobTaskTone, TaskProgressSummary, TaskProgressTable } from '../components/TaskProgressTable'
import type { TaskTone } from '../components/TaskProgressTable'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import { useAdmissions, stopAdmission, useQueueError } from '../processing/workQueue'
import type { QueueAdmission } from '../processing/workQueue'
import { readJournal } from '../processing/journal'
import { requestBatchStop } from '../components/BatchProcessor'
import type { BatchProgressSnapshot, BatchExecution } from '../components/BatchProcessor'
import type { SingleWorkflow } from '../processing/singleWorkflow'
import { stopSingleWorkflow } from '../processing/singleWorkflow'

const LABELS = { queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', cancelled: '已停止' }

/** Includes automatic/legacy schedulers whose pending windows have no DB job yet. */
function legacyAdmissions(current: QueueAdmission[]): { items: QueueAdmission[]; errors: string[] } {
  const ids = new Set(current.map(item => item.id))
  const errors: string[] = []
  const items = Object.keys(localStorage).flatMap(key => {
    try {
    if (key.startsWith('ndr:tasks:v1:batch:')) {
      const bookId = key.slice('ndr:tasks:v1:batch:'.length)
      const saved = readJournal<{ execution: BatchExecution; snapshot: BatchProgressSnapshot }>(`batch:${bookId}`)
      if (!saved?.execution || ids.has(saved.execution.queueId ?? '')) return []
      return [{ id: `legacy:batch:${bookId}:${saved.snapshot.startedAt}`, bookId,
        versionId: saved.execution.bookVersionId, title: saved.execution.expandable ? '自动提前处理' : '已有批量范围',
        keys: [], phase: saved.snapshot.running ? 'running' : saved.snapshot.tasks.some(task => task.state === 'failed') ? 'failed' : saved.snapshot.stopRequested ? 'cancelled' : 'completed',
        payload: { type: 'batch', work: saved.execution }, error: null, createdAt: saved.snapshot.startedAt,
        stopRequested: saved.snapshot.stopRequested } as QueueAdmission]
    }
    if (key.startsWith('ndr:tasks:v1:single:')) {
      const bookId = key.slice('ndr:tasks:v1:single:'.length)
      const saved = readJournal<SingleWorkflow>(`single:${bookId}`)
      if (!saved || ids.has(saved.queueId ?? '')) return []
      return [{ id: `legacy:single:${bookId}`, bookId, versionId: saved.versionId, title: '已有单章范围', keys: [],
        phase: saved.running ? 'running' : saved.stopRequested ? 'cancelled' : saved.error ? 'failed' : 'completed',
        payload: { type: 'single', work: saved }, error: saved.error, createdAt: 0, stopRequested: saved.stopRequested } as QueueAdmission]
    }
    return []
    } catch (error) {
      errors.push(error instanceof Error ? error.message : '保存的范围无法读取')
      return []
    }
  })
  return { items, errors }
}

function ChapterTaskHistory({ item, chapterId, kind, windowId, onSelect }: {
  item: QueueAdmission; chapterId: string; kind: 'CHARACTER_ROSTER' | 'INFERENCE';
  windowId?: string | null; onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false)
  const jobs = useQuery({ queryKey: ['range-chapter-history', item.versionId, chapterId, kind],
    enabled: open, queryFn: ({ signal }) => fetchRecentJobs({ bookId: item.bookId,
      versionId: item.versionId, chapterId, kind, limit: 200 }, signal) })
  const matching = jobs.data?.filter(job => !windowId || !Array.isArray(job.range?.selected_window_ids)
    || job.range.selected_window_ids.includes(windowId))
  return <CollapsibleBlock title="本章后台任务" summary="查询此章节实际保存的任务与恢复入口" open={open} onOpenChange={setOpen}>
      <p className="hint">旧范围没有保存完整任务明细。以下是本章最近的后台任务，不代表它们全部属于此批次。</p>
      {jobs.isPending && <p role="status">正在读取任务…</p>}
      {jobs.isError && <ReadErrorNotice label="本章任务读取失败" error={jobs.error} retrying={jobs.isFetching} onRetry={() => void jobs.refetch()} />}
      <PaginatedItems label="本章后台任务" scope={`${item.id}:${chapterId}:${kind}`}>
      {matching?.map(job => <div key={job.id}>{JOB_STATE_LABELS[job.state]} · {new Date(job.created_at).toLocaleString()}
        <button onClick={() => onSelect(job.id)}>查看任务</button>{job.last_error && <p className="status-error">{job.last_error}</p>}</div>)}
      </PaginatedItems>
      {matching?.length === 0 && <p className="hint">未找到匹配的后台任务。</p>}
  </CollapsibleBlock>
}

function admissionTasks(item: QueueAdmission) {
  if (item.payload.type === 'merge') return [{ id: 'merge', chapter: '全书', type: '自动合并人物', window: '—', tone: item.phase as TaskTone, state: LABELS[item.phase], error: item.error, jobId: item.jobId }]
  const batch = readJournal<{ execution: { queueId?: string }; snapshot: BatchProgressSnapshot }>(`batch:${item.bookId}`)
  const single = readJournal<SingleWorkflow>(`single:${item.bookId}`)
  const plannedTone = item.phase === 'queued' ? 'queued' : 'unknown'
  const plannedLabel = item.phase === 'queued' ? LABELS.queued : '状态待核实'
  const batchTasks = batch && (batch.execution.queueId === item.id || item.id === `legacy:batch:${item.bookId}:${batch.snapshot.startedAt}`)
    ? batch.snapshot.tasks : readBatchHistory(item.id)
  return item.payload.type === 'batch'
    ? batchTasks ? batchTasks.map(task => ({ id: task.id, chapterId: task.chapterId, kind: task.type === 'roster' ? 'CHARACTER_ROSTER' as const : 'INFERENCE' as const, windowId: task.windowId, chapter: task.chapterTitle, type: task.type === 'roster' ? '人物识别' : '对白归属', window: task.windowLabel, tone: task.state as TaskTone, state: LABELS[task.state], error: task.error, jobId: task.jobId }))
: item.payload.work.plans.flatMap(plan => [{ id: `roster:${plan.chapter.id}`, chapterId: plan.chapter.id, kind: 'CHARACTER_ROSTER' as const, windowId: null, chapter: plan.chapter.title, type: '人物识别', window: '全文', tone: plannedTone as TaskTone, state: plannedLabel, error: null, jobId: null }, ...(plan.estimate.windows ?? []).map(window => ({ id: `dialogue:${plan.chapter.id}:${window.window_id}`, chapterId: plan.chapter.id, kind: 'INFERENCE' as const, windowId: window.window_id, chapter: plan.chapter.title, type: '对白归属', window: `窗口 ${window.ordinal}`, tone: plannedTone as TaskTone, state: plannedLabel, error: null, jobId: null }))])
    : (single && (single.queueId === item.id || item.id === `legacy:single:${item.bookId}`
      && single.queueId === item.payload.work.queueId && single.versionId === item.payload.work.versionId
      && single.chapterId === item.payload.work.chapterId
      && single.tasks[0]?.input.idempotencyKey === item.payload.work.tasks[0]?.input.idempotencyKey) ? single : item.payload.work).tasks.map(task => ({ id: task.windowId, chapter: item.title, type: '对白归属', window: `窗口 ${task.ordinal}`, tone: task.job ? jobTaskTone(task.job.state) : task.error ? 'failed' as TaskTone : item.phase, state: task.job ? JOB_STATE_LABELS[task.job.state] : task.error ? '未执行' : LABELS[item.phase], error: task.error, jobId: task.job?.id }))
}

type AdmissionTask = ReturnType<typeof admissionTasks>[number]
type SelectedTask = { jobId: string } | { item: QueueAdmission; task: AdmissionTask }
function useTaskRefresh(enabled = true) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!enabled) return
    const timer = setInterval(() => setTick(value => value + 1), 2000)
    return () => clearInterval(timer)
  }, [enabled])
}

function PlannedTaskDetails({ item, initialTask, onSelect }: { item: QueueAdmission; initialTask: AdmissionTask; onSelect: (id: string) => void }) {
  useTaskRefresh()
  const current = admissionTasks(item).find(task => task.id === initialTask.id)
  if (current?.jobId) return <JobPanel jobId={current.jobId} />
  const task = current ?? initialTask
  return <>
    <TaskProgressTable label="当前任务" rows={[{ id: task.id, state: task.tone, stateLabel: task.state,
      type: task.type, chapter: task.chapter || '未命名章节', window: task.window, error: task.error }]} />
    <p className="hint" role="status">{!current ? '原任务记录已更新或缺失，无法确认当前状态。'
      : task.tone === 'queued' ? '本任务尚未派发，轮到它执行后会在这里显示模型任务详情。'
      : task.tone === 'running' ? '本任务正在准备提交，收到后台任务回执后会自动显示详情。'
      : '本任务没有保存后台任务回执，请核实原记录后再处理。'}</p>
    {item.payload.type === 'merge' && <p className="hint">等待本书前序处理结束后分析人物，生成建议后仍需确认。</p>}
    {task.tone !== 'queued' && task.tone !== 'running' && 'chapterId' in task && task.chapterId && <ChapterTaskHistory item={item}
      chapterId={task.chapterId} kind={task.kind} windowId={typeof task.windowId === 'string' ? task.windowId : null} onSelect={onSelect} />}
  </>
}

function AdmissionDetails({ item, onSelect, onSelectPlanned }: { item: QueueAdmission; onSelect: (id: string) => void;
  onSelectPlanned: (item: QueueAdmission, task: AdmissionTask) => void }) {
  useTaskRefresh(['queued', 'running'].includes(item.phase))
  const tasks = admissionTasks(item)
  const failed = tasks.filter(task => task.tone === 'failed').length
  return <>
    <TaskProgressSummary total={tasks.length} finished={tasks.filter(task => ['completed', 'failed', 'cancelled'].includes(task.tone)).length}
      running={tasks.filter(task => task.tone === 'running').length} label={`${item.title}任务进度`} />
    <CollapsibleBlock title="范围内任务" defaultOpen={false} summary={`共 ${tasks.length} 项；失败 ${failed} 项`}>
      <FilterableTaskProgressTable label="范围内任务" scope={item.id} rows={tasks.map(task => ({ id: task.id,
        state: task.tone, stateLabel: task.state, type: task.type, chapter: task.chapter || '未命名章节', window: task.window,
        error: task.error, actions: <button onClick={() => task.jobId ? onSelect(task.jobId) : onSelectPlanned(item, task)}>查看任务</button> }))} />
    </CollapsibleBlock>
  </>
}

export default function TaskQueuePage() {
  const [searchParams] = useSearchParams()
  const admissions = useAdmissions()
  const runtimeError = useQueueError()
  // Observe the startup inventory without re-requesting it on progress ticks.
  const queryClient = useQueryClient()
  const inventory = useSyncExternalStore(
    listener => queryClient.getQueryCache().subscribe(listener),
    () => queryClient.getQueryData<ReadonlySet<string>>(['task-recovery-books']),
  )
  const [legacy, setLegacy] = useState(() => legacyAdmissions(admissions))
  useEffect(() => {
    setLegacy(legacyAdmissions(admissions))
    const timer = setInterval(() => setLegacy(legacyAdmissions(admissions)), 3000)
    return () => clearInterval(timer)
  }, [admissions])
  const [history, setHistory] = useState(false)
  const [selected, setSelected] = useState<SelectedTask | null>(() => searchParams.get('jobId') ? { jobId: searchParams.get('jobId')! } : null)
  useEffect(() => { if (searchParams.get('jobId')) setSelected({ jobId: searchParams.get('jobId')! }) }, [searchParams])
  const [detailRequest, setDetailRequest] = useState(0)
  const detailRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!selected) return
    detailRef.current?.scrollIntoView?.({ block: 'start' })
    detailRef.current?.focus({ preventScroll: true })
  }, [selected, detailRequest])
  const [error, setError] = useState<string | null>(null)
  const jobs = useInfiniteQuery({ queryKey: ['task-queue', history], initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => fetchTaskQueue(!history, pageParam, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
    refetchInterval: query => !history || query.state.data?.pages.some(page => page.items.some(job => ['QUEUED', 'RUNNING', 'PAUSING'].includes(job.state))) ? 2000 : false })
  const ranges = [...admissions, ...legacy.items.filter(item => !inventory || inventory.has(item.bookId))].sort((a, b) => b.createdAt - a.createdAt)
  const visible = ranges.filter(item => history || ['queued', 'running'].includes(item.phase))
  const select = (id: string) => { setSelected({ jobId: id }); setDetailRequest(value => value + 1) }
  const selectPlanned = (item: QueueAdmission, task: AdmissionTask) => { setSelected({ item, task }); setDetailRequest(value => value + 1) }
  const stop = async (item: QueueAdmission) => {
    if (!item.id.startsWith('legacy:')) return stopAdmission(item.id)
    if (item.payload.type === 'batch') {
      const current = readJournal<{ execution: BatchExecution; snapshot: BatchProgressSnapshot }>(`batch:${item.bookId}`)
      if (!current || `legacy:batch:${item.bookId}:${current.snapshot.startedAt}` !== item.id
        || current.execution.queueId !== item.payload.work.queueId) throw new Error('本书范围已更新，请重新查看后停止。')
      requestBatchStop(item.bookId)
    } else {
      if (item.payload.type !== 'single' || readJournal<SingleWorkflow>(`single:${item.bookId}`)?.queueId !== item.payload.work.queueId) throw new Error('本书范围已更新，请重新查看后停止。')
      await stopSingleWorkflow(item.bookId)
    }
  }
  return <>
    <header className="card ndr-page-header"><div><h2>任务队列</h2><p>查看所有书籍的处理进度；可以继续添加其他章节或窗口，同一窗口不会重复入队。</p></div></header>
    <section className="card">
      <label className="ndr-field"><span><input type="checkbox" checked={history} onChange={event => setHistory(event.target.checked)} /> 显示已结束任务</span></label>
      <p className="hint">保持应用页面打开，等待范围会继续派发；切换页面和刷新不会丢失已保存的队列。同一本书的处理范围依次执行，不同书籍共享并发额度。停止后已发出的模型请求仍可能计费。</p>
      {error && <p role="alert" className="status-error">{error}</p>}
      {runtimeError && <p role="alert" className="status-error">{runtimeError}</p>}
      {legacy.errors.length > 0 && <p role="alert" className="status-error">有 {legacy.errors.length} 份旧范围记录无法读取，原记录保留，未从这些记录派发新任务。请先检查保存的任务，不要重复提交。</p>}
      <CollapsibleBlock title="已添加的处理范围" summary={`${ranges.filter(item => ['queued', 'running'].includes(item.phase)).length} 个范围等待或执行中；${ranges.filter(item => item.phase === 'failed').length} 个范围失败（勾选“显示已结束任务”查看）`}>
        {!visible.length && <p>没有{history ? '保存的' : '等待或执行中的'}处理范围。</p>}
        <PaginatedItems label="处理范围" pageSize={5} scope={String(history)}>
        {visible.map(item => <article className="card ndr-batch-progress ndr-task-queue-range" key={item.id}>
          <div className="ndr-step-heading"><strong>{item.bookTitle ? `${item.bookTitle} · ` : ''}{item.title}</strong><span className={`ndr-task-state ndr-task-${item.phase}`}>{item.stopRequested && item.phase === 'running' ? '正在安全停止' : LABELS[item.phase]}</span>
            <Link className="ndr-button" to={`/books/${item.bookId}/preview`}>打开本书</Link>
            {['queued', 'running'].includes(item.phase) && <button className="ndr-danger" disabled={item.stopRequested} title={item.stopRequested ? '停止请求已提交，请等待在途任务收尾。' : undefined} onClick={() => { void stop(item).catch(err => setError(err.message)) }}>{item.phase === 'queued' ? '取消范围' : '停止范围'}</button>}
          </div>
          {item.error && <p className="status-error">{item.error}</p>}
          {item.waiting && <p className="hint" role="status">{item.waiting.reason}{item.waiting.title && ` 等待任务：${item.waiting.title}`}
            {item.waiting.jobId && <button onClick={() => select(item.waiting!.jobId!)}>查看等待任务</button>}</p>}
          <AdmissionDetails item={item} onSelect={select} onSelectPlanned={selectPlanned} />
        </article>)}
        </PaginatedItems>
      </CollapsibleBlock>
      <CollapsibleBlock title="后台任务" summary="包含各书籍的人物识别、对白归属、局部复核及自动合并；详情按需读取。">
        {jobs.isPending && <p role="status">正在读取任务…</p>}
        {jobs.isError && <ReadErrorNotice label="任务队列读取失败" error={jobs.error} retrying={jobs.isFetching} onRetry={() => { void jobs.refetch() }} />}
        {jobs.data?.pages.every(page => !page.items.length) && <p>没有{history ? '' : '排队或运行的'}后台任务。</p>}
        <TaskProgressTable label="后台任务" scope={String(history)} showBook
          hasMore={jobs.hasNextPage} loading={jobs.isFetchingNextPage} loadMore={async () => {
            const result = await jobs.fetchNextPage()
            if (result.isError) throw result.error
          }} rows={(jobs.data?.pages.flatMap(page => page.items) ?? []).map(job => ({ id: job.id,
            state: jobTaskTone(job.state), stateLabel: JOB_STATE_LABELS[job.state], type: JOB_KIND_LABELS[job.kind],
            book: job.book_title || '书籍已删除', chapter: job.chapter_title || '全书',
            window: job.windows_total ? <span>窗口 {job.windows_done}/{job.windows_total}</span> : '—',
            error: job.last_error, actions: <button onClick={() => select(job.id)}>查看任务</button>,
          }))} />
      </CollapsibleBlock>
    </section>
    {selected && <section className="card" key={'jobId' in selected ? selected.jobId : `${selected.item.id}:${selected.task.id}`} ref={detailRef} tabIndex={-1}>
      <h2>任务详情</h2><button onClick={() => setSelected(null)}>关闭详情</button>
      {'jobId' in selected ? <JobPanel jobId={selected.jobId} /> : <PlannedTaskDetails item={selected.item.id.startsWith('legacy:') ? selected.item : ranges.find(item => item.id === selected.item.id) ?? selected.item}
        initialTask={selected.task} onSelect={select} />}
    </section>}
  </>
}
