import { useInfiniteQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { fetchTaskQueue } from '../api/jobs'
import { JobPanel, JOB_KIND_LABELS, JOB_STATE_LABELS } from '../components/JobPanel'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import { useAdmissions, stopAdmission, useQueueError } from '../processing/workQueue'
import type { QueueAdmission } from '../processing/workQueue'
import { readJournal } from '../processing/journal'
import { requestBatchStop } from '../components/BatchProcessor'
import type { BatchProgressSnapshot, BatchExecution } from '../components/BatchProcessor'
import type { SingleWorkflow } from '../processing/singleWorkflow'
import { stopSingleWorkflow } from '../processing/singleWorkflow'

const LABELS = { queued: '等待执行', running: '执行中', completed: '已完成', failed: '失败', cancelled: '已停止' }

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

function AdmissionDetails({ item, onSelect }: { item: QueueAdmission; onSelect: (id: string) => void }) {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!['queued', 'running'].includes(item.phase)) return
    const timer = setInterval(() => setTick(value => value + 1), 2000)
    return () => clearInterval(timer)
  }, [item.phase])
  void tick
  if (item.payload.type === 'merge') return item.jobId ? <button onClick={() => onSelect(item.jobId!)}>查看任务</button> : <p className="hint">等待本书前序处理结束后分析人物，生成建议后仍需确认。</p>
  const batch = readJournal<{ execution: { queueId?: string }; snapshot: BatchProgressSnapshot }>(`batch:${item.bookId}`)
  const single = readJournal<SingleWorkflow>(`single:${item.bookId}`)
  const tasks = item.payload.type === 'batch'
    ? batch && (batch.execution.queueId === item.id || item.id.startsWith('legacy:')) ? batch.snapshot.tasks.map(task => ({ id: task.id, label: `${task.chapterTitle} · ${task.type === 'roster' ? '人物识别' : `对白 ${task.windowLabel}`}`, state: LABELS[task.state], error: task.error, jobId: task.jobId }))
      : item.payload.work.plans.flatMap(plan => [{ id: `roster:${plan.chapter.id}`, label: `${plan.chapter.title} · 人物识别`, state: LABELS[item.phase], error: null, jobId: null }, ...(plan.estimate.windows ?? []).map(window => ({ id: `${plan.chapter.id}:${window.window_id}`, label: `${plan.chapter.title} · 对白窗口 ${window.ordinal}`, state: LABELS[item.phase], error: null, jobId: null }))])
    : (single && (single.queueId === item.id || item.id.startsWith('legacy:')) ? single : item.payload.work).tasks.map(task => ({ id: task.windowId, label: `对白窗口 ${task.ordinal}`, state: task.job ? JOB_STATE_LABELS[task.job.state] : task.error ? '未执行' : LABELS[item.phase], error: task.error, jobId: task.job?.id }))
  return <CollapsibleBlock title="范围内任务" defaultOpen={false} summary={`共 ${tasks.length} 项`}>
    <ul className="ndr-task-queue-list">{tasks.map(task => <li key={task.id}>
      <span>{task.label} · {task.state}</span>
      {task.jobId && <button onClick={() => onSelect(task.jobId!)}>查看任务</button>}
      {task.error && <p className="status-error">{task.error}</p>}
    </li>)}</ul>
  </CollapsibleBlock>
}

export default function TaskQueuePage() {
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
  const [selected, setSelected] = useState<string | null>(null)
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
  const select = (id: string) => { setSelected(id); setDetailRequest(value => value + 1) }
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
        {visible.map(item => <article className="card ndr-task-queue-range" key={item.id}>
          <div className="ndr-form-actions"><strong>{item.bookTitle ? `${item.bookTitle} · ` : ''}{item.title}</strong><span>{item.stopRequested && item.phase === 'running' ? '正在安全停止' : LABELS[item.phase]}</span>
            <Link className="ndr-button" to={`/books/${item.bookId}/preview`}>打开本书</Link>
            {['queued', 'running'].includes(item.phase) && <button className="ndr-danger" disabled={item.stopRequested} title={item.stopRequested ? '停止请求已提交，请等待在途任务收尾。' : undefined} onClick={() => { void stop(item).catch(err => setError(err.message)) }}>{item.phase === 'queued' ? '取消范围' : '停止范围'}</button>}
          </div>
          {item.error && <p className="status-error">{item.error}</p>}
          <AdmissionDetails item={item} onSelect={select} />
        </article>)}
      </CollapsibleBlock>
      <CollapsibleBlock title="后台任务" summary="包含各书籍的人物识别、对白归属、局部复核及自动合并；详情按需读取。">
        {jobs.isPending && <p role="status">正在读取任务…</p>}
        {jobs.isError && <ReadErrorNotice label="任务队列读取失败" error={jobs.error} retrying={jobs.isFetching} onRetry={() => { void jobs.refetch() }} />}
        {jobs.data?.pages.every(page => !page.items.length) && <p>没有{history ? '' : '排队或运行的'}后台任务。</p>}
        <ul className="ndr-task-queue-list">{jobs.data?.pages.flatMap(page => page.items).map(job => <li key={job.id}>
          <div><strong>{job.book_title || '书籍已删除'}</strong> · {job.chapter_title || '全书'} · {JOB_KIND_LABELS[job.kind]} · {JOB_STATE_LABELS[job.state]}</div>
          {Boolean(job.windows_total) && <span>窗口 {job.windows_done}/{job.windows_total}</span>}
          {job.selected_window_ids?.length ? <span>窗口：{job.selected_window_ids.join('、')}</span> : null}
          {job.last_error && <p className="status-error">{job.last_error}</p>}
          <button onClick={() => select(job.id)}>查看任务</button>
        </li>)}</ul>
        {jobs.hasNextPage && <button disabled={jobs.isFetchingNextPage} title={jobs.isFetchingNextPage ? '正在读取下一页，请稍候。' : undefined} onClick={() => { void jobs.fetchNextPage() }}>加载更多</button>}
      </CollapsibleBlock>
    </section>
    {selected && <section className="card" key={selected} ref={detailRef} tabIndex={-1}><h2>任务详情</h2><button onClick={() => setSelected(null)}>关闭详情</button><JobPanel jobId={selected} /></section>}
  </>
}
