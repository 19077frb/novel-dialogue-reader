import { useEffect, useSyncExternalStore } from 'react'
import { completeChapterProcessing, fetchBook, fetchJob } from '../api/books'
import { createJob } from '../api/jobs'
import type { CreateJobInput } from '../api/jobs'
import type { JobDetailOut } from '../api/types'
import { mapWithConcurrency } from './concurrency'
import { waitForJobCompletion } from './jobCompletion'
import { readJournal, writeJournal, withWorkflowLock, assertWorkflowOwnership } from './journal'

export interface SingleWorkflow {
  bookId: string; versionId: string; chapterId: string | null
  startCp: number; endCp: number; mode: 'preview' | 'process'
  concurrency: number; completeChapter: boolean; running: boolean; error: string | null
  tasks: { windowId: string; ordinal: string; input: CreateJobInput; job: JobDetailOut | null; error: string | null }[]
}
const snapshots = new Map<string, SingleWorkflow>()
const listeners = new Set<() => void>()
const executions = new Map<string, Promise<JobDetailOut>>()
function publish(work: SingleWorkflow) {
  assertWorkflowOwnership(`processing:${work.bookId}`)
  const snapshot = { ...work, tasks: work.tasks.map(task => ({ ...task })) }
  writeJournal(`single:${work.bookId}`, { ...snapshot, tasks: snapshot.tasks.map(task => ({ ...task,
    job: task.job ? { ...task.job, checkpoint: undefined, windows: [] } : null })) })
  snapshots.set(work.bookId, snapshot)
  listeners.forEach(listener => listener())
}

export function runSingleWorkflow(work: SingleWorkflow): Promise<JobDetailOut> {
  const active = executions.get(work.bookId)
  if (active) return active
  publish(work)
  const execution = withWorkflowLock(`processing:${work.bookId}`, async () => {
    const latest = readJournal<SingleWorkflow>(`single:${work.bookId}`)
    if (latest && !latest.running) { Object.assign(work, latest); return latest.tasks.at(-1)!.job! }
    const book = await fetchBook(work.bookId)
    if (book.active_version_id !== work.versionId) throw new Error('书籍版本已改变，未继续原单章任务。')
    let stopDispatch = false
    const jobs = await mapWithConcurrency(work.tasks, work.concurrency, async task => {
      if (stopDispatch && !task.job) { task.error = '前序任务结果尚不明确，本窗口未派发'; publish(work); return null }
      try {
        // The input, including request key and allocated budget, was persisted before POST.
        const initial = task.job ? await fetchJob(task.job.id) : await createJob(task.input)
        return await waitForJobCompletion(initial, job => { task.job = job; publish(work) })
      } catch (error) {
        stopDispatch = true
        task.error = error instanceof Error ? error.message : String(error)
        publish(work)
        return null
      }
    })
    const failed = jobs.find(job => job && job.state !== 'COMPLETED')
    if (failed || jobs.some(job => !job)) throw new Error(failed?.last_error ?? (failed ? `窗口任务结束于 ${failed.state}` : '任务结果尚不明确，请查看窗口任务详情，勿重复提交'))
    if (work.completeChapter && work.chapterId) await completeChapterProcessing(work.bookId, work.chapterId, work.versionId)
    return jobs.at(-1)!
  }).catch(error => {
    work.error = error instanceof Error ? error.message : String(error)
    throw error
  }).finally(() => { work.running = false; try { publish(work) } finally { executions.delete(work.bookId) } })
  executions.set(work.bookId, execution)
  return execution
}

export function restoreSingleWorkflow(bookId: string) {
  if (executions.has(bookId)) return
  const work = readJournal<SingleWorkflow>(`single:${bookId}`)
  if (!work || work.bookId !== bookId || !Array.isArray(work.tasks)) return
  snapshots.set(bookId, work)
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

export function hasSingleWork() { return [...snapshots.values()].some(work => work.running) }

export function useSingleWorkflow(bookId?: string) {
  useEffect(() => {
    if (!bookId) return
    if (!executions.has(bookId) && !readJournal(`single:${bookId}`)) {
      snapshots.delete(bookId)
      listeners.forEach(listener => listener())
    } else if (!snapshots.has(bookId)) restoreSingleWorkflow(bookId)
  }, [bookId])
  return useSyncExternalStore(listener => { listeners.add(listener); return () => listeners.delete(listener) },
    () => bookId && localStorage.getItem(`ndr:tasks:v1:single:${bookId}`) ? snapshots.get(bookId) ?? null : null)
}
