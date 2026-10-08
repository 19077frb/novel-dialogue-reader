/** Persistent admissions. Credentials and novel text must never enter these records. */
import { useSyncExternalStore } from 'react'
import type { SingleWorkflow } from './singleWorkflow'
import type { BatchExecution } from '../components/BatchProcessor'
import type { CharacterAutoMergeIn } from '../api/types'
import { readJournal, writeJournal, withWorkflowLock } from './journal'
import { pruneBatchHistory } from './batchHistory'
import { fetchBook } from '../api/books'
import { waitForJobCompletion } from './jobCompletion'
import { inSharedTaskPool } from './concurrency'
import { getProcessingPreferences } from './preferences'

type Payload = { type: 'single'; work: SingleWorkflow }
  | { type: 'batch'; work: BatchExecution }
  | { type: 'merge'; input: CharacterAutoMergeIn }
export interface QueueAdmission {
  id: string; bookId: string; versionId: string; title: string; keys: string[]
  phase: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  payload: Payload; error: string | null; jobId?: string; createdAt: number; stopRequested?: boolean
  bookTitle?: string
}
const KEY = 'admissions'
const listeners = new Set<() => void>()
const executing = new Set<string>()
const blockedBooks = new Set<string>()
let runtimeError: string | null = null
let snapshot: QueueAdmission[] = []
let raw: string | null = null
export function getAdmissions(): QueueAdmission[] {
  const next = localStorage.getItem(`ndr:tasks:v1:${KEY}`)
  if (raw !== next) {
    const items = readJournal<QueueAdmission[]>(KEY) ?? []
    if (!Array.isArray(items)) throw new Error('保存的任务队列格式无效，请勿重复启动模型任务。')
    raw = next; snapshot = items
  }
  return snapshot
}
function notify() { getAdmissions(); listeners.forEach(listener => listener()) }
async function change(id: string, patch: Partial<QueueAdmission>, allowed?: QueueAdmission['phase'][]) {
  return withWorkflowLock('admissions', async () => {
    if (allowed && !getAdmissions().some(item => item.id === id && allowed.includes(item.phase))) return false
    writeJournal(KEY, getAdmissions().map(item => item.id === id ? { ...item, ...patch } : item))
    notify()
    return true
  })
}
export function useAdmissions() {
  return useSyncExternalStore(listener => {
    listeners.add(listener)
    const changed = () => notify()
    window.addEventListener('storage', changed)
    return () => { listeners.delete(listener); window.removeEventListener('storage', changed) }
  }, getAdmissions)
}

export function useQueueError() {
  return useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener) }, () => runtimeError)
}

/** Release a finished window even while other windows in its range are active. */
export function occupiedAdmissionKeys(item: QueueAdmission): string[] {
  if (item.phase !== 'running') return item.keys
  if (item.payload.type === 'batch') {
    const saved = readJournal<{ execution: BatchExecution; snapshot: { tasks: { id: string; state: string }[] } }>(`batch:${item.bookId}`)
    if (saved?.execution.queueId === item.id) return saved.snapshot.tasks.filter(task => ['queued', 'running'].includes(task.state)).map(task => task.id)
  } else if (item.payload.type === 'single') {
    const saved = readJournal<SingleWorkflow>(`single:${item.bookId}`)
    if (saved?.queueId === item.id) return saved.tasks.filter(task => !task.job || ['QUEUED', 'RUNNING', 'PAUSING', 'NEEDS_RECONCILIATION'].includes(task.job.state)).map(task => `dialogue:${saved.chapterId}:${task.windowId}`)
  }
  return item.keys
}

export async function enqueueWork(input: Omit<QueueAdmission, 'id' | 'phase' | 'error' | 'createdAt'>) {
  // Serialize duplicate detection across tabs before saving any execution plan.
  const id = await withWorkflowLock('admissions', async () => {
    const items = getAdmissions()
    const duplicate = items.find(item => ['queued', 'running'].includes(item.phase)
      && item.bookId === input.bookId && item.versionId === input.versionId
      && occupiedAdmissionKeys(item).some(key => input.keys.includes(key)))
    if (duplicate) throw new Error(`该窗口已有任务在任务队列中等待执行或正在执行：${duplicate.title}。请取消重复窗口选择或前往任务队列查看。`)
    // Include legacy browser queues that predate admissions.
    const batch = readJournal<{ execution: BatchExecution; snapshot: { running: boolean; tasks: { id: string; state: string }[] } }>(`batch:${input.bookId}`)
    const single = readJournal<SingleWorkflow>(`single:${input.bookId}`)
    const occupied = batch?.snapshot.running ? batch.snapshot.tasks.filter(task => ['queued', 'running'].includes(task.state)).map(task => task.id) : []
    if (single?.running) occupied.push(...single.tasks.filter(task => !task.job || ['QUEUED', 'RUNNING', 'PAUSING'].includes(task.job.state)).map(task => `dialogue:${single.chapterId}:${task.windowId}`))
    if (occupied.some(key => input.keys.includes(key))) throw new Error('该窗口已有任务在任务队列中等待执行或正在执行，请前往任务队列查看。')
    const id = crypto.randomUUID()
    // JSON snapshot removes UI callbacks and freezes options at admission time.
    const payload = input.payload.type === 'batch' ? { ...input.payload, work: { ...input.payload.work,
      plans: input.payload.work.plans.map(plan => ({ ...plan, estimate: { ...plan.estimate,
        windows: (plan.estimate.windows ?? []).map(window => ({ window_id: window.window_id,
          ordinal: window.ordinal, target_count: window.target_count, estimated_tokens: window.estimated_tokens,
          processing_status: window.processing_status })) } })) } } : input.payload
    const bookTitle = input.bookTitle || items.find(item => item.bookId === input.bookId && item.bookTitle)?.bookTitle
    const item: QueueAdmission = JSON.parse(JSON.stringify({ ...input, bookTitle, payload, id, phase: 'queued', error: null, createdAt: Date.now() }))
    const active = items.filter(item => ['queued', 'running'].includes(item.phase))
    const history = items.filter(item => !['queued', 'running'].includes(item.phase)).slice(-200)
    writeJournal(KEY, [...active, ...history, item].sort((a, b) => a.createdAt - b.createdAt))
    pruneBatchHistory(new Set([...active, ...history, item].map(row => row.id)))
    notify()
    return id
  })
  void pumpQueue()
  return id
}

export async function cancelAdmission(id: string) {
  if (!await change(id, { phase: 'cancelled' }, ['queued'])) throw new Error('本范围已开始执行，请停止其正在执行的任务。')
}

export async function stopAdmission(id: string) {
  const item = getAdmissions().find(item => item.id === id)
  if (!item || !['queued', 'running'].includes(item.phase)) return
  if (item.phase === 'queued' && await change(id, { phase: 'cancelled' }, ['queued'])) return
  await change(id, { stopRequested: true })
  if (item.payload.type === 'single') {
    const { stopSingleWorkflow } = await import('./singleWorkflow')
    if (readJournal<SingleWorkflow>(`single:${item.bookId}`)?.queueId === id) await stopSingleWorkflow(item.bookId)
  } else if (item.payload.type === 'batch') {
    const { requestBatchStop } = await import('../components/BatchProcessor')
    if (readJournal<{ execution: BatchExecution }>(`batch:${item.bookId}`)?.execution.queueId === id) requestBatchStop(item.bookId)
  } else if (item.jobId) {
    const { pauseJob } = await import('../api/jobs')
    await pauseJob(item.jobId)
  }
}

async function execute(id: string) {
  await withWorkflowLock(`admission:${id}`, async () => {
    const item = getAdmissions().find(item => item.id === id)
    if (!item || !['queued', 'running'].includes(item.phase)) return
    let cancelled = false
    try {
      const book = await fetchBook(item.bookId)
      if (book.active_version_id !== item.versionId) throw new Error('书籍版本已改变，未派发旧范围。')
      if (!await change(id, { phase: 'running', bookTitle: book.title }, ['queued', 'running'])) return
      if (item.payload.type === 'single') {
        const { runSingleWorkflow } = await import('./singleWorkflow')
        const saved = readJournal<SingleWorkflow>(`single:${item.bookId}`)
        const work = saved?.queueId === id ? saved : { ...item.payload.work, queueId: id }
        const job = await runSingleWorkflow(work)
        await change(id, { jobId: job.id })
      } else if (item.payload.type === 'batch') {
        const { runBatchProcessing, restoreBatchProcessing } = await import('../components/BatchProcessor')
        const saved = readJournal<{ execution: BatchExecution; snapshot: { running: boolean; tasks: { state: string }[] } }>(`batch:${item.bookId}`)
        if (saved?.execution.queueId === id) {
          if (saved.snapshot.running) await restoreBatchProcessing(item.bookId)
        } else await runBatchProcessing({ ...item.payload.work, queueId: id })
        // A route's recovery effect may already own this workflow. Wait for its
        // journal instead of mistaking the early return for completion.
        while (readJournal<typeof saved>(`batch:${item.bookId}`)?.snapshot.running) {
          await new Promise(resolve => setTimeout(resolve, 800))
        }
        const result = readJournal<typeof saved>(`batch:${item.bookId}`)
        if (result?.snapshot.tasks.some(task => task.state === 'failed')) throw new Error('部分任务失败，请展开批量任务明细查看原因。')
        cancelled = Boolean(result?.snapshot.tasks.some(task => task.state === 'cancelled'))
      } else {
        const { startCharacterAutoMerge } = await import('../api/characters')
        const { fetchJob } = await import('../api/books')
        const input = item.payload.input
        await withWorkflowLock(`processing:${item.bookId}`, () => inSharedTaskPool(getProcessingPreferences().concurrency, async () => {
          // Direct roster/recheck submissions also live in the backend. Wait for
          // them instead of dropping a merge behind an unrelated active job.
          const { fetchTaskQueue } = await import('../api/jobs')
          while (!item.jobId && (await fetchTaskQueue(true, null, undefined, item.bookId)).items.length) {
            if (getAdmissions().find(row => row.id === id)?.stopRequested) return
            await new Promise(resolve => setTimeout(resolve, 1000))
          }
          if (getAdmissions().find(row => row.id === id)?.stopRequested) return
          const initial = item.jobId ? await fetchJob(item.jobId)
            : await startCharacterAutoMerge(item.bookId, input)
          await change(id, { jobId: initial.id })
          const { pauseJob } = await import('../api/jobs')
          const current = getAdmissions().find(row => row.id === id)?.stopRequested ? await pauseJob(initial.id) : initial
          const job = await waitForJobCompletion(current, () => undefined)
          if (job.state !== 'COMPLETED') throw new Error(job.last_error || `自动合并结束于${job.state}`)
        }))
      }
      await change(id, { phase: cancelled || getAdmissions().find(row => row.id === id)?.stopRequested ? 'cancelled' : 'completed' })
      window.dispatchEvent(new CustomEvent('ndr:queue-completed', { detail: { bookId: item.bookId } }))
    } catch (error) {
      await change(id, { phase: getAdmissions().find(row => row.id === id)?.stopRequested ? 'cancelled' : 'failed', error: error instanceof Error ? error.message : String(error) })
    }
  })
}

export async function pumpQueue(existingBooks?: ReadonlySet<string>) {
  const items = getAdmissions()
  for (const item of items) {
    if (!['queued', 'running'].includes(item.phase) || executing.has(item.bookId) || blockedBooks.has(item.bookId)) continue
    if (existingBooks && !existingBooks.has(item.bookId)) {
      await change(item.id, { phase: 'failed', error: '书籍已不存在，未派发任务。' }); continue
    }
    executing.add(item.bookId)
    // Old single/batch recovery uses the inner processing lock too; do not replace its journal.
    void withWorkflowLock(`admission-book:${item.bookId}`, () => execute(item.id))
      .catch(error => {
        blockedBooks.add(item.bookId)
        runtimeError = `任务队列无法继续保存，已停止本书的新派发：${error instanceof Error ? error.message : String(error)}。请检查浏览器存储后刷新恢复；不要重复提交。`
        listeners.forEach(listener => listener())
      })
      .finally(() => { executing.delete(item.bookId); if (!blockedBooks.has(item.bookId)) void pumpQueue() })
  }
}
