import { useEffect, useSyncExternalStore } from 'react'
import { completeChapterProcessing, fetchBook, fetchJob, setChapterProcessingStatus } from '../api/books'
import { createJob, pauseJob, fetchRecentJobs } from '../api/jobs'
import type { CreateJobInput } from '../api/jobs'
import type { JobDetailOut } from '../api/types'
import { mapWithConcurrency, inSharedTaskPool } from './concurrency'
import { waitForJobCompletion } from './jobCompletion'
import { readJournal, writeJournal, withWorkflowLock, assertWorkflowOwnership } from './journal'
import { getAdmissions } from './workQueue'

export interface SingleWorkflow {
  queueId?: string
  clearManualStatus?: boolean
  stopRequested?: boolean
  bookId: string; versionId: string; chapterId: string | null
  startCp: number; endCp: number; mode: 'preview' | 'process'
  concurrency: number; completeChapter: boolean; running: boolean; error: string | null
  tasks: { windowId: string; ordinal: string; input: CreateJobInput; job: JobDetailOut | null; error: string | null }[]
}
const snapshots = new Map<string, SingleWorkflow>()
const snapshotSources = new Map<string, string>()
const listeners = new Set<() => void>()
const executions = new Map<string, Promise<JobDetailOut>>()
const localStopObservers = new Map<string, () => void>()
function workflowIdentity(work: SingleWorkflow) {
  return JSON.stringify(work.queueId ? ['queue', work.queueId]
    : ['legacy', work.versionId, work.tasks.map(task => task.input.idempotencyKey)])
}
function stopRequested(work: SingleWorkflow) {
  return Boolean(work.stopRequested
    || readJournal<{ workflow: string }>(`single-stop:${work.bookId}`)?.workflow === workflowIdentity(work)
    || work.queueId && getAdmissions().find(item => item.id === work.queueId)?.stopRequested)
}
function publish(work: SingleWorkflow) {
  assertWorkflowOwnership(`processing:${work.bookId}`)
  const snapshot = { ...work, tasks: work.tasks.map(task => ({ ...task })) }
  writeJournal(`single:${work.bookId}`, { ...snapshot, tasks: snapshot.tasks.map(task => ({ ...task,
    job: task.job ? { ...task.job, checkpoint: undefined, windows: [] } : null })) })
  snapshots.set(work.bookId, snapshot)
  snapshotSources.delete(work.bookId)
  listeners.forEach(listener => listener())
}
function savedSnapshot(bookId: string) {
  const raw = localStorage.getItem(`ndr:tasks:v1:single:${bookId}`)
  if (!raw) { snapshots.delete(bookId); snapshotSources.delete(bookId); return null }
  const source = JSON.stringify([raw, localStorage.getItem(`ndr:tasks:v1:single-stop:${bookId}`),
    localStorage.getItem('ndr:tasks:v1:admissions')])
  if (snapshotSources.get(bookId) !== source) {
    const work = readJournal<SingleWorkflow>(`single:${bookId}`)
    if (!work || work.bookId !== bookId || !Array.isArray(work.tasks)) throw new Error('保存的单章任务格式无效，请勿重复启动模型任务。')
    snapshots.set(bookId, { ...work, stopRequested: stopRequested(work) })
    snapshotSources.set(bookId, source)
  }
  return snapshots.get(bookId) ?? null
}

export function runSingleWorkflow(work: SingleWorkflow): Promise<JobDetailOut> {
  const active = executions.get(work.bookId)
  if (active) return active.catch(() => undefined).then(() => runSingleWorkflow(work))
  const execution = withWorkflowLock(`processing:${work.bookId}`, async () => {
    const latest = readJournal<SingleWorkflow>(`single:${work.bookId}`)
    const cancelled = stopRequested(work)
    if (cancelled && work.queueId && latest?.queueId !== work.queueId) throw new Error('本范围已取消，未派发窗口。')
    // A second tab may have waited for the owner to finish, including legacy ranges.
    if (latest && workflowIdentity(latest) === workflowIdentity(work) && !latest.running) {
      if (latest.error) throw new Error(latest.error)
      return latest.tasks.at(-1)!.job!
    }
    work.stopRequested = cancelled
    let controlError: Error | null = null
    const stopped = () => {
      try { if (stopRequested(work)) work.stopRequested = true }
      catch (error) {
        controlError = error instanceof Error ? error : new Error(String(error))
        work.stopRequested = true
      }
      return Boolean(work.stopRequested)
    }
    const pauseRequests = new Map<string, Promise<void>>()
    const pauseKnownJobs = () => {
      for (const task of work.tasks) {
        const job = task.job
        if (!job || !['QUEUED', 'RUNNING'].includes(job.state) || pauseRequests.has(job.id)) continue
        pauseRequests.set(job.id, pauseJob(job.id).then(() => undefined).catch(error => {
          task.error = `暂停请求失败：${error instanceof Error ? error.message : String(error)}；仍在等待已提交任务收尾。`
          publish(work)
        }))
      }
    }
    const observeStop = () => {
      if (!stopped()) return
      publish(work)
      pauseKnownJobs()
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === 'ndr:tasks:v1:admissions'
        || event.key === `ndr:tasks:v1:single-stop:${work.bookId}`) observeStop()
    }
    localStopObservers.set(work.bookId, observeStop)
    window.addEventListener('storage', onStorage)
    try {
      publish(work)
      const book = await fetchBook(work.bookId)
      if (book.active_version_id !== work.versionId) throw new Error('书籍版本已改变，未继续原单章任务。')
      if (work.clearManualStatus && work.chapterId && !stopped()) await setChapterProcessingStatus(work.bookId, work.chapterId, work.versionId, null)
      let stopDispatch = false
      const jobs = await mapWithConcurrency(work.tasks, work.concurrency, task => inSharedTaskPool(work.concurrency, async () => {
        try {
          if (stopped() && !task.job) {
            // Resolve lost acknowledgements without issuing another POST.
            task.job = (await fetchRecentJobs({ bookId: work.bookId, versionId: work.versionId,
              idempotencyKey: task.input.idempotencyKey, limit: 1 }))[0] ?? null
            if (!task.job) { task.error = '已取消，本窗口未派发'; publish(work); return null }
          }
          if (stopDispatch && !task.job) { task.error = '前序任务结果尚不明确，本窗口未派发'; publish(work); return null }
          // No asynchronous gap between the last stop check and the POST.
          const initial = task.job ? await fetchJob(task.job.id) : await createJob(task.input)
          task.job = initial
          if (stopped()) pauseKnownJobs()
          const result = await waitForJobCompletion(initial, job => {
            task.job = job
            if (stopped()) pauseKnownJobs()
            publish(work)
          })
          window.dispatchEvent(new CustomEvent('ndr:window-completed', { detail: {
            bookId: work.bookId, startCp: work.startCp, endCp: work.endCp,
          } }))
          return result
        } catch (error) {
          stopDispatch = true
          task.error = error instanceof Error ? error.message : String(error)
          publish(work)
          return null
        }
      }))
      await Promise.all(pauseRequests.values())
      const failed = jobs.find(job => job && job.state !== 'COMPLETED')
      if (stopped()) throw controlError ?? new Error('本范围已安全停止，已保存结果保留。')
      if (failed || jobs.some(job => !job)) throw new Error(failed?.last_error ?? (failed ? `窗口任务结束于 ${failed.state}` : '任务结果尚不明确，请查看窗口任务详情，勿重复提交'))
      if (work.completeChapter && work.chapterId) await completeChapterProcessing(work.bookId, work.chapterId, work.versionId)
      return jobs.at(-1)!
    } catch (error) {
      work.error = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      window.removeEventListener('storage', onStorage)
      localStopObservers.delete(work.bookId)
      work.running = false; publish(work)
    }
  }).finally(() => { executions.delete(work.bookId) })
  executions.set(work.bookId, execution)
  return execution
}

export async function stopSingleWorkflow(bookId: string) {
  // A spectator may have a stale snapshot: never write it over the owner's journal.
  const work = readJournal<SingleWorkflow>(`single:${bookId}`)
  if (!work?.running) return
  writeJournal(`single-stop:${bookId}`, { workflow: workflowIdentity(work) })
  listeners.forEach(listener => listener())
  const observe = localStopObservers.get(bookId)
  if (observe) { observe(); return }
  await Promise.all(work.tasks.filter(task => task.job &&
    ['QUEUED', 'RUNNING', 'PAUSING'].includes(task.job.state)).map(task => pauseJob(task.job!.id)))
}

export function restoreSingleWorkflow(bookId: string) {
  if (executions.has(bookId)) return
  const work = savedSnapshot(bookId)
  if (!work) return
  listeners.forEach(listener => listener())
  if (work.running) void runSingleWorkflow(work).catch(() => undefined)
}

export function restoreSavedSingles(existingBookIds: ReadonlySet<string>) {
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith('ndr:tasks:v1:single:')) continue
    const bookId = key.slice('ndr:tasks:v1:single:'.length)
    if (existingBookIds.has(bookId)) restoreSingleWorkflow(bookId)
  }
}

export function hasSingleWork() { return [...snapshots.keys()].some(bookId => savedSnapshot(bookId)?.running) }

export function useSingleWorkflow(bookId?: string) {
  useEffect(() => {
    if (bookId && !executions.has(bookId)) restoreSingleWorkflow(bookId)
  }, [bookId])
  return useSyncExternalStore(listener => {
    listeners.add(listener)
    window.addEventListener('storage', listener)
    return () => { listeners.delete(listener); window.removeEventListener('storage', listener) }
  }, () => bookId ? savedSnapshot(bookId) : null)
}
