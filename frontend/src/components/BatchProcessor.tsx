import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { Link } from 'react-router-dom'
import { saveBatchHistory } from '../processing/batchHistory'

import { completeChapterProcessing, fetchBook, fetchChapters, fetchProcessingStatus, setChapterProcessingStatus } from '../api/books'
import { ApiError } from '../api/client'
import { fetchJob } from '../api/books'
import {
  analyzeCharacterRoster,
  confirmCharacterRoster,
  fetchCharacterRoster,
} from '../api/characters'
import { createJob, estimateRange, freshIdempotencyKey, pauseJob, fetchRecentJobs, dialogueStrategyDisabledReason } from '../api/jobs'
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'
import { hasSingleWork } from '../processing/singleWorkflow'
import type { ChapterOut, EstimateOut, JobDetailOut, ModelProfileOut } from '../api/types'
import { createTaskLimiter, mapWithConcurrency, inSharedTaskPool } from '../processing/concurrency'
import { waitForJobCompletion } from '../processing/jobCompletion'
import { getProcessingPreferences, inferenceOptions, useProcessingPreferences, rosterRepairOptions, estimateRosterTokens } from '../processing/preferences'
import type { ProcessingPreferences } from '../processing/preferences'
import { OperationTimer } from './OperationTimer'
import { readJournal, writeJournal, removeJournal, withWorkflowLock, assertWorkflowOwnership } from '../processing/journal'
import type { AnalyzeRosterInput } from '../api/characters'
import type { CreateJobInput } from '../api/jobs'
import { FormatRetrySetting } from './FormatRetrySetting'
import { CollapsibleBlock } from './CollapsibleBlock'
import { getGeneralSettings, useGeneralSettings } from '../settings/preferences'
import { chapterFilterReason, defaultChapterFilter, normalizeChapterFilter } from '../processing/chapterFilter'
import type { ChapterFilter } from '../processing/chapterFilter'
import { enqueueWork, getAdmissions } from '../processing/workQueue'

class BatchAbortError extends Error {}

function isBatchAbortError(reason: unknown): reason is BatchAbortError {
  return reason instanceof BatchAbortError
}
const TASK_STATE_LABELS: Record<BatchTaskState, string> = {
  queued: '排队中',
  running: '处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
}

export type ChapterProcessingState =
  | 'unprocessed'
  | 'queued'
  | 'roster'
  | 'dialogue'
  | 'processed'
  | 'failed'
  | 'stopped'
  | 'skipped'
export interface ChapterProcessingProgress {
  state: ChapterProcessingState
  completedWindows: number
  totalWindows: number
  error: string | null
  cancelRequested?: boolean
  pendingTasks?: number
  // Only a new admission may supersede the previous manual display override.
  manualStatusCleared?: boolean
}
export type BatchTaskState = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type BatchTaskType = 'roster' | 'dialogue'

export interface BatchTaskProgress {
  id: string
  type: BatchTaskType
  chapterId: string
  chapterTitle: string
  windowId: string | null
  windowLabel: string
  state: BatchTaskState
  error: string | null
  retryable?: boolean
  jobId?: string | null
}

export interface BatchProgressSnapshot {
  startedAt: number
  finishedAt: number | null
  running: boolean
  stopRequested: boolean
  message: string
  chapterStates: Record<string, ChapterProcessingProgress>
  annotationRevisions: Record<string, number>
  catalogRevision: number
  tasks: BatchTaskProgress[]
  revision: number
}

const EMPTY_BATCH: BatchProgressSnapshot = {
  startedAt: 0,
  finishedAt: null,
  running: false,
  stopRequested: false,
  message: '',
  chapterStates: {},
  annotationRevisions: {},
  catalogRevision: 0,
  tasks: [],
  revision: 0,
}
const batchSnapshots = new Map<string, BatchProgressSnapshot>()
const batchListeners = new Set<() => void>()
const batchStopRequests = new Set<string>()
const retryPlanning = new Set<string>()
const retryAdmissions = new Map<string, Promise<unknown>>()
const retryStopGenerations = new Map<string, number>()
const chapterRetryStops = new Map<string, number>()
const batchExecutions = new Map<string, { execution: BatchExecution; spent: number; totalSpent: number; unknownRuns: number }>()
const chapterStopRequests = new Map<string, Set<string>>()
const expandableBatches = new Map<string, {
  versionId: string; automatic: boolean; append: (plans: ChapterPlan[]) => number
  retry: (plan: ChapterPlan) => void; cancel: (chapterId: string) => void
}>()
const batchRequests = new Map<string, Record<string, { type: 'roster' | 'dialogue'; input: AnalyzeRosterInput | CreateJobInput; jobId?: string }>>()
const accountedJobs = new Map<string, Set<string>>()
const restoringBatches = new Set<string>()

interface SavedBatch {
  schema: 1; snapshot: BatchProgressSnapshot; execution: BatchExecution
  requests: Record<string, { type: 'roster' | 'dialogue'; input: AnalyzeRosterInput | CreateJobInput; jobId?: string }>
  accounted: string[]; spent: number; totalSpent: number; unknownRuns: number
}

function persistBatch(bookId: string) {
  assertWorkflowOwnership(`processing:${bookId}`)
  const saved = batchExecutions.get(bookId)
  const snapshot = batchSnapshots.get(bookId)
  if (!saved || !snapshot) return
  const execution = { ...saved.execution, plans: saved.execution.plans.map(plan => ({ ...plan,
    estimate: { ...plan.estimate, windows: (plan.estimate.windows ?? []).map(window => ({
      window_id: window.window_id, ordinal: window.ordinal, target_count: window.target_count,
      estimated_tokens: window.estimated_tokens, processing_status: window.processing_status,
    })) } })) }
  writeJournal(`batch:${bookId}`, { schema: 1, snapshot, execution,
    requests: batchRequests.get(bookId) ?? {}, accounted: [...(accountedJobs.get(bookId) ?? [])],
    spent: saved.spent, totalSpent: saved.totalSpent, unknownRuns: saved.unknownRuns } satisfies SavedBatch)
  if (execution.queueId) saveBatchHistory(execution.queueId, snapshot.tasks)
}

/** Resume the original requests, never replace their idempotency keys. */
export async function restoreBatchProcessing(bookId: string) {
  if (restoringBatches.has(bookId) || expandableBatches.has(bookId)) return
  const saved = readJournal<SavedBatch>(`batch:${bookId}`)
  if (!saved || saved.schema !== 1 || saved.execution.bookId !== bookId) return
  batchSnapshots.set(bookId, saved.snapshot)
  batchListeners.forEach(listener => listener())
  batchExecutions.set(bookId, { execution: saved.execution, spent: saved.spent,
    totalSpent: saved.totalSpent, unknownRuns: saved.unknownRuns })
  batchRequests.set(bookId, saved.requests)
  accountedJobs.set(bookId, new Set(saved.accounted))
  restoringBatches.add(bookId)
  try {
    await withWorkflowLock(`processing:${bookId}`, async () => {
      // Another tab may have finished while this tab waited for the lock.
      const latest = readJournal<SavedBatch>(`batch:${bookId}`)
      if (!latest) return
      const resumeQueue = latest.snapshot.running
      batchSnapshots.set(bookId, latest.snapshot)
      const book = await fetchBook(bookId)
      if (book.active_version_id !== latest.execution.bookVersionId) throw new Error('书籍版本已改变，未继续旧队列。')
      // Reconcile acknowledged and lost-ack submissions before dispatching anything new.
      for (const [taskId, request] of Object.entries(latest.requests)) {
        const recorded = latest.snapshot.tasks.find(task => task.id === taskId)
        if (recorded?.state === 'completed' && request.jobId && latest.accounted.includes(request.jobId)) continue
        let job = request.jobId ? await fetchJob(request.jobId) : (await fetchRecentJobs({ bookId,
          versionId: latest.execution.bookVersionId, idempotencyKey: request.input.idempotencyKey, limit: 1 }))[0]
        if (!job) continue
        request.jobId = job.id
        const cancelled = latest.snapshot.stopRequested || latest.snapshot.chapterStates[
          latest.snapshot.tasks.find(task => task.id === taskId)?.chapterId ?? '']?.cancelRequested
        if (cancelled && !TERMINAL_JOB_STATES.has(job.state)) { await pauseJob(job.id); job = await waitForJob(job) }
        if (!TERMINAL_JOB_STATES.has(job.state)) {
          // Old requests already occupy physical provider slots. Drain them before
          // reconstructing the pool so reload cannot exceed the saved concurrency.
          job = await waitForJobCompletion(job, current => {
            if (current.state === 'COMPLETED' && taskId.startsWith('dialogue:')) {
              const task = batchSnapshots.get(bookId)?.tasks.find(task => task.id === taskId)
              if (task && task.state !== 'completed') { updateBatchTask(bookId, taskId, 'completed'); recordCompletedWindow(bookId, task.chapterId) }
            }
          })
        }
        // The backend may have finished after the old page last saved its snapshot.
        // Reconcile already-terminal jobs too, not just jobs observed while polling.
        const task = batchSnapshots.get(bookId)?.tasks.find(item => item.id === taskId)
        if (task && TERMINAL_JOB_STATES.has(job.state)) {
          updateBatchTask(bookId, taskId, job.state === 'COMPLETED' ? 'completed' : cancelled && job.state === 'PAUSED' ? 'cancelled' : 'failed', job.last_error ?? null)
          const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
          publishBatch(bookId, { tasks: current.tasks.map(item => item.id === taskId ? { ...item,
            retryable: job.state === 'FAILED' && !(latest.execution.preferences.tokenLimit !== null && job.unknown_usage_runs > 0) } : item) })
          if (job.state === 'COMPLETED' && task.type === 'dialogue' && task.state !== 'completed') recordCompletedWindow(bookId, task.chapterId)
        }
        if (TERMINAL_JOB_STATES.has(job.state) && !latest.accounted.includes(job.id)) {
          const tokens = jobTokens(job)
          latest.spent += tokens; latest.totalSpent += tokens
          latest.unknownRuns += job.unknown_usage_runs ?? 0
          latest.accounted.push(job.id)
        }
      }
      // Keep stop/cancel changes made while the old requests were draining.
      latest.snapshot = readJournal<SavedBatch>(`batch:${bookId}`)?.snapshot ?? latest.snapshot
      const chapters = await fetchChapters(bookId)
      for (const chapter of chapters) {
        const progress = latest.snapshot.chapterStates[chapter.id]
        if (!progress) continue
        const tasks = latest.snapshot.tasks.filter(task => task.chapterId === chapter.id)
        const pending = tasks.filter(task => ['queued', 'running'].includes(task.state)).length
        if (chapter.dialogue_processed && pending === 0) {
          latest.snapshot.chapterStates[chapter.id] = { ...progress, state: 'processed', completedWindows: progress.totalWindows,
            pendingTasks: 0, cancelRequested: false, error: null }
          latest.snapshot.catalogRevision += 1
        } else if (!resumeQueue && ['queued', 'roster', 'dialogue'].includes(progress.state)) {
          latest.snapshot.chapterStates[chapter.id] = { ...progress, state: tasks.some(task => task.state === 'failed') ? 'failed' : 'stopped', pendingTasks: 0, cancelRequested: false }
        }
      }
      for (const [chapterId, progress] of Object.entries(latest.snapshot.chapterStates)) {
        if (!progress.cancelRequested && progress.state !== 'stopped') continue
        latest.snapshot.chapterStates[chapterId] = { ...progress, state: 'stopped', cancelRequested: false, pendingTasks: 0 }
        latest.snapshot.tasks = latest.snapshot.tasks.map(task => task.chapterId === chapterId && ['queued', 'running'].includes(task.state)
          ? { ...task, state: 'cancelled' } : task)
      }
      writeJournal(`batch:${bookId}`, latest)
      batchExecutions.set(bookId, { execution: latest.execution, spent: latest.spent,
        totalSpent: latest.totalSpent, unknownRuns: latest.unknownRuns })
      batchRequests.set(bookId, latest.requests)
      accountedJobs.set(bookId, new Set(latest.accounted))
      if (!resumeQueue) {
        batchSnapshots.set(bookId, latest.snapshot)
        publishBatch(bookId, { tasks: latest.snapshot.tasks.map(task => ['queued', 'running'].includes(task.state) ? { ...task, state: 'cancelled' } : task) })
        return
      }
      if (latest.execution.preferences.tokenLimit !== null && latest.unknownRuns > 0) throw new Error('已有调用用量未知，不能可靠继续限额队列，请先检查任务详情。')
      if (latest.snapshot.stopRequested) {
        batchSnapshots.set(bookId, latest.snapshot)
        publishBatch(bookId, { running: false, stopRequested: false, finishedAt: Date.now(), message: '批量任务已安全停止；没有派发后续任务。',
          tasks: latest.snapshot.tasks.map(task => ['queued', 'running'].includes(task.state) ? { ...task, state: 'cancelled' } : task) })
        return
      }
      batchSnapshots.set(bookId, latest.snapshot)
      await runBatchInternal({ ...latest.execution, restoring: true,
        initialSpent: latest.spent, initialTotalSpent: latest.totalSpent, initialUnknownRuns: latest.unknownRuns })
    })
  } catch (error) {
    const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
    publishBatch(bookId, { running: false,
      tasks: current.tasks.map(task => task.state === 'running' ? { ...task, state: 'failed', retryable: false,
        error: '任务状态未能核实，请查看任务详情；不要重复启动模型调用。' } : task.state === 'queued' ? { ...task, state: 'cancelled' } : task),
      chapterStates: Object.fromEntries(Object.entries(current.chapterStates).map(([id, progress]) => [id,
        ['queued', 'roster', 'dialogue'].includes(progress.state) ? { ...progress, state: 'stopped', pendingTasks: 0, cancelRequested: false } : progress])),
      message: `任务恢复失败：${error instanceof Error ? error.message : String(error)}。原任务记录保留，请勿重复提交。` })
  } finally { restoringBatches.delete(bookId) }
}

export function restoreSavedBatches(existingBookIds: ReadonlySet<string>) {
  for (const key of Object.keys(localStorage)) {
    if (!key.startsWith('ndr:tasks:v1:batch:')) continue
    const bookId = key.slice('ndr:tasks:v1:batch:'.length)
    if (existingBookIds.has(bookId)) void restoreBatchProcessing(bookId)
  }
}

async function submitBatchRequest(bookId: string, taskId: string, type: 'roster' | 'dialogue', input: AnalyzeRosterInput | CreateJobInput): Promise<JobDetailOut> {
  const requests = batchRequests.get(bookId) ?? {}
  batchRequests.set(bookId, requests)
  const saved = requests[taskId] ?? (requests[taskId] = { type, input })
  persistBatch(bookId) // Write before POST, including its stable request key and budget.
  if (saved.jobId) return fetchJob(saved.jobId)
  const job = type === 'roster'
    ? await analyzeCharacterRoster(bookId, taskId.slice('roster:'.length), saved.input as AnalyzeRosterInput)
    : await createJob(saved.input as CreateJobInput)
  saved.jobId = job.id
  persistBatch(bookId)
  return job
}

function chapterShouldStop(bookId: string, chapterId: string) {
  return chapterStopRequests.get(bookId)?.has(chapterId) ?? false
}

export function hasChapterQueueWork(bookId: string, chapterId: string) {
  return expandableBatches.has(bookId) && (batchSnapshots.get(bookId)?.tasks ?? [])
    .some(task => task.chapterId === chapterId && ['queued', 'running'].includes(task.state))
}

export function hasUnresolvedChapterResult(bookId: string, versionId: string, chapterId: string) {
  if (batchExecutions.get(bookId)?.execution.bookVersionId !== versionId) return false
  return (batchSnapshots.get(bookId)?.tasks ?? []).some(task => task.chapterId === chapterId
    && task.state === 'failed' && !task.retryable)
}

export function synchronizeManualChapterStatus(bookId: string, chapter: ChapterOut) {
  updateChapterProgress(bookId, chapter.id, { state: chapter.dialogue_processed ? 'processed' : 'unprocessed',
    manualStatusCleared: false, error: null })
}

function finishChapterCancellation(bookId: string, chapterId: string) {
  if (!chapterShouldStop(bookId, chapterId)) return
  const tasks = batchSnapshots.get(bookId)?.tasks ?? []
  if (tasks.some(task => task.chapterId === chapterId && task.state === 'running')) return
  updateChapterProgress(bookId, chapterId, { state: 'stopped', cancelRequested: false,
    error: '本章任务已取消，已保存的标注保留。' })
}

export async function cancelChapterProcessing(bookId: string, chapterId: string) {
  const retryKey = `${bookId}:${chapterId}`
  chapterRetryStops.set(retryKey, (chapterRetryStops.get(retryKey) ?? 0) + 1)
  const controller = expandableBatches.get(bookId)
  if (!controller && restoringBatches.has(bookId)) {
    const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
    updateChapterProgress(bookId, chapterId, { cancelRequested: true, state: 'stopped' })
    publishBatch(bookId, { tasks: current.tasks.map(task => task.chapterId === chapterId && task.state === 'queued' ? { ...task, state: 'cancelled' } : task) })
    await Promise.all(current.tasks.filter(task => task.chapterId === chapterId && task.jobId && task.state === 'running').map(task => pauseJob(task.jobId!)))
    return
  }
  if (!controller) throw new Error('本章没有可取消的运行队列，请刷新目录。')
  if (!chapterStopRequests.has(bookId)) chapterStopRequests.set(bookId, new Set())
  chapterStopRequests.get(bookId)!.add(chapterId)
  controller.cancel(chapterId)
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  const active = current.tasks.filter(task => task.chapterId === chapterId && task.state === 'running')
  publishBatch(bookId, { tasks: current.tasks.map(task => task.chapterId === chapterId && task.state === 'queued'
    ? { ...task, state: 'cancelled' } : task), message: '已取消本章后续任务；其他章节继续处理。' })
  updateChapterProgress(bookId, chapterId, { cancelRequested: active.length > 0, pendingTasks: active.length, state: active.length ? current.chapterStates[chapterId]?.state ?? 'dialogue' : 'stopped',
    error: active.length ? '正在取消本章任务；已发出的请求安全收尾，不保证停止计费。' : '本章任务已取消。' })
  const results = await Promise.allSettled(active.filter(task => task.jobId).map(task => pauseJob(task.jobId!)))
  finishChapterCancellation(bookId, chapterId)
  const failed = results.find(result => result.status === 'rejected')
  if (failed?.status === 'rejected') throw new Error(`暂停请求失败，已阻止本章后续派发，当前请求将继续收尾：${failed.reason instanceof Error ? failed.reason.message : String(failed.reason)}`)
}

export async function retryChapterProcessing(bookId: string, chapterId: string) {
  const snapshot = batchSnapshots.get(bookId)
  const tasks = snapshot?.tasks.filter(task => task.chapterId === chapterId) ?? []
  if (!tasks.length) throw new Error('本章任务记录已清除，请在预览与处理中选择未完成窗口。')
  if (tasks.some(task => ['queued', 'running'].includes(task.state))) throw new Error('请等待本章任务安全收尾后再重试。')
  if (tasks.some(task => task.state === 'failed' && !task.retryable)) throw new Error('本章有结果尚不明确的任务，请先在预览与处理中查看详情，避免重复调用。')
  await retryBatchTask(bookId, tasks[0].id, true)
}

export function canAppendAutomaticProcessing(bookId: string, versionId: string) {
  return expandableBatches.get(bookId)?.automatic === true && expandableBatches.get(bookId)?.versionId === versionId && !batchStopRequests.has(bookId)
}

export function automaticChapterFilter(bookId: string): ChapterFilter | undefined {
  const execution = batchExecutions.get(bookId)?.execution
  return execution?.expandable && isBatchRunning(bookId) ? execution.chapterFilter ?? defaultChapterFilter() : undefined
}

/** Extend the existing pool, never start a second pool or reset its allowance. */
export function appendAutomaticProcessing(bookId: string, versionId: string, plans: ChapterPlan[]): number {
  if (!canAppendAutomaticProcessing(bookId, versionId)) return 0
  const strategy = batchExecutions.get(bookId)?.execution.preferences.dialogueStrategy ?? 'legacy'
  if (plans.some(plan => Boolean(plan.estimate.policy?.full_source) !== (strategy !== 'legacy')
    || Boolean(plan.estimate.policy?.dialogue_blocks) !== strategy.startsWith('complete-blocks')
    || plan.estimate.policy?.auxiliary_protocol !== (strategy.includes('-isolated') ? 'expression-auxiliary-isolation-1' : undefined)
    || (plan.estimate.policy?.review_protocol === 'expression-evidence-review-1') !== strategy.endsWith('-review')
    || plan.estimate.policy?.identity_feedback_protocol !== (strategy === 'complete-blocks-isolated-feedback-review' ? 'identity-feedback-1' : undefined))) {
    publishBatch(bookId, { message: '当前批次已固定对白策略；新策略将在本批次结束后用于下一批，未追加不匹配的窗口。' })
    return 0
  }
  return expandableBatches.get(bookId)!.append(plans)
}

/** Saved chapter completion supersedes obsolete failures, including manual repairs. */
export function reconcileCompletedChapter(bookId: string, chapterId: string) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  publishBatch(bookId, {
    tasks: current.tasks.map(task => task.chapterId === chapterId ? { ...task, state: 'completed', error: null, retryable: false } : task),
    chapterStates: { ...current.chapterStates, [chapterId]: {
      state: 'processed', completedWindows: current.chapterStates[chapterId]?.totalWindows ?? 0,
      totalWindows: current.chapterStates[chapterId]?.totalWindows ?? 0, error: null, pendingTasks: 0, cancelRequested: false,
    } },
    catalogRevision: current.catalogRevision + 1,
  })
}

export async function retryBatchTask(bookId: string, taskId: string, wholeChapter = false) {
  const task = batchSnapshots.get(bookId)?.tasks.find(item => item.id === taskId)
  if (!task) throw new Error('任务记录已清除，请重新读取。')
  const prefix = `${bookId}:${task.chapterId}:`
  const key = `${prefix}${wholeChapter ? '*' : taskId}`
  if (retryPlanning.has(key) || retryPlanning.has(`${prefix}*`)
    || wholeChapter && [...retryPlanning].some(item => item.startsWith(prefix))) {
    throw new Error('本任务正在准备重试，请等待加入队列。')
  }
  retryPlanning.add(key)
  const stopGeneration = retryStopGenerations.get(bookId) ?? 0
  const chapterStopGeneration = chapterRetryStops.get(`${bookId}:${task.chapterId}`) ?? 0
  // Serialize only admission, never the model execution. The first admission
  // installs the pool synchronously; subsequent clicks reuse it and its budget.
  const admission = (retryAdmissions.get(bookId) ?? Promise.resolve()).catch(() => undefined)
    .then(() => {
      if ((retryStopGenerations.get(bookId) ?? 0) !== stopGeneration) throw new Error('已停止准备重试，未调用模型。')
      return prepareBatchRetry(bookId, taskId, wholeChapter, stopGeneration, chapterStopGeneration)
    })
  retryAdmissions.set(bookId, admission)
  let admitted: { completion?: Promise<void> }
  try { admitted = await admission } finally {
    retryPlanning.delete(key)
    if (retryAdmissions.get(bookId) === admission) retryAdmissions.delete(bookId)
  }
  await admitted.completion
}

async function prepareBatchRetry(bookId: string, taskId: string, wholeChapter: boolean, stopGeneration: number, chapterStopGeneration: number): Promise<{ completion?: Promise<void> }> {
  let controller = expandableBatches.get(bookId)
  if (batchSnapshots.get(bookId)?.stopRequested) throw new Error('队列正在停止，请等待请求收尾后再重试。')
  const saved = batchExecutions.get(bookId)
  const task = batchSnapshots.get(bookId)?.tasks.find(item => item.id === taskId)
  if (!saved || !task || (!wholeChapter && (task.state !== 'failed' || !task.retryable))) throw new Error('此任务不能直接重试，请先查看任务详情并确认后台请求已结束。')
  if ((chapterRetryStops.get(`${bookId}:${task.chapterId}`) ?? 0) !== chapterStopGeneration) throw new Error('已取消本章重试，未调用模型。')
  if (!wholeChapter && (batchSnapshots.get(bookId)?.tasks ?? []).some(item => item.chapterId === task.chapterId
    && item.type === 'roster' && ['queued', 'running'].includes(item.state))) throw new Error('请等待本章人物识别完成后再重试窗口。')
  if (!controller) publishBatch(bookId, { running: true, startedAt: Date.now(), finishedAt: null, stopRequested: false,
    message: '正在检查失败任务与已保存的窗口（不调用模型）…' })
  try {
    const [book, chapters, status] = await Promise.all([fetchBook(bookId), fetchChapters(bookId), fetchProcessingStatus(bookId)])
    if (status.active_jobs > 0 && !controller) throw new Error('后台仍有任务运行，请等待结束后重试。')
    if (book.active_version_id !== saved.execution.bookVersionId) throw new Error('书籍版本已改变，请重新进入预览与处理。')
    const chapter = chapters.find(item => item.id === task.chapterId)
    if (!chapter) throw new Error('章节已不存在，请重新读取目录。')
    const filterReason = chapterFilterReason(chapter.title, getGeneralSettings())
    if (filterReason) throw new Error(filterReason)
    const estimate = await estimateRange(bookId, { bookVersionId: saved.execution.bookVersionId,
      range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp, dialogueStrategy: saved.execution.preferences.dialogueStrategy }, readingMode: 'reread',
      budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: saved.execution.preferences.maxRecheckRounds,
        maxRechecks: (saved.execution.preferences as ProcessingPreferences & { maxRechecks?: number }).maxRechecks,
        maxFormatRetries: saved.execution.preferences.maxFormatRetries } })
    const windows = estimate.windows ?? []
    if (batchStopRequests.has(bookId)) throw new Error('已停止准备重试，未调用模型。')
    const forceReprocess = saved.execution.forceReprocess ?? false
    if (chapter.dialogue_processed && !forceReprocess) {
      reconcileCompletedChapter(bookId, chapter.id)
      if (!controller) publishBatch(bookId, { running: false, finishedAt: Date.now() })
      saved.execution.onFinished?.(); return {}
    }
    const chapterTasks = batchSnapshots.get(bookId)?.tasks.filter(item => item.chapterId === chapter.id) ?? []
    // Chapter retry repairs the previous task set, not every newly estimated window.
    const repairWindowIds = new Set(chapterTasks.filter(item => item.type === 'dialogue'
      && ['failed', 'cancelled'].includes(item.state)).map(item => item.windowId))
    const selected = windows.filter(window => (forceReprocess
      ? batchSnapshots.get(bookId)?.tasks.find(item => item.chapterId === chapter.id && item.windowId === String(window.window_id))?.state !== 'completed'
      : window.processing_status !== 'completed')
      && (wholeChapter ? repairWindowIds.has(String(window.window_id)) : task.type === 'roster' || String(window.window_id) === task.windowId))
    if (!wholeChapter && task.type === 'dialogue' && !windows.some(window => String(window.window_id) === task.windowId)) {
      throw new Error('窗口计划已变化，请进入单章处理选择未完成窗口。')
    }
    const plan: ChapterPlan = { chapter,
      estimate: { ...estimate, windows: selected }, totalWindows: windows.length,
      completedWindows: windows.filter(window => forceReprocess
        ? batchSnapshots.get(bookId)?.tasks.find(item => item.chapterId === chapter.id && item.windowId === String(window.window_id))?.state === 'completed'
        : window.processing_status === 'completed').length,
      reuseRoster: (wholeChapter || task.type === 'dialogue') && (await fetchCharacterRoster(bookId, chapter.id, saved.execution.bookVersionId)).status === 'CONFIRMED' }
    if (!wholeChapter && task.type === 'dialogue' && !plan.reuseRoster) throw new Error('请先确认本章人物，再重试对白窗口。')
    // The old pool may have drained during the read-only checks.
    controller = expandableBatches.get(bookId)
    if ((retryStopGenerations.get(bookId) ?? 0) !== stopGeneration) throw new Error('已停止准备重试，未调用模型。')
    if ((chapterRetryStops.get(`${bookId}:${task.chapterId}`) ?? 0) !== chapterStopGeneration) throw new Error('已取消本章重试，未调用模型。')
    if (controller) {
      if (batchStopRequests.has(bookId)) throw new Error('队列正在停止，请等待收尾。')
      controller.retry(plan)
      return {}
    } else {
      chapterStopRequests.get(bookId)?.delete(chapter.id)
      publishBatch(bookId, { running: false })
      const latest = batchExecutions.get(bookId) ?? saved
      let ready!: () => void
      const poolReady = new Promise<void>(resolve => { ready = resolve })
      const completion = withWorkflowLock(`processing:${bookId}`, () => {
        if ((retryStopGenerations.get(bookId) ?? 0) !== stopGeneration) throw new Error('已停止准备重试，未调用模型。')
        if ((chapterRetryStops.get(`${bookId}:${task.chapterId}`) ?? 0) !== chapterStopGeneration) throw new Error('已取消本章重试，未调用模型。')
        const reason = dialogueStrategyDisabledReason(latest.execution.preferences.dialogueStrategy, latest.execution.preferences.maxRecheckRounds)
        if (reason) throw new Error(reason)
        return runBatchInternal({ ...latest.execution, requested: [chapter], plans: [plan],
          chapterFilter: normalizeChapterFilter(latest.execution.chapterFilter ?? getGeneralSettings()),
          forceReprocess, initialSpent: latest.spent, initialTotalSpent: latest.totalSpent,
          initialUnknownRuns: latest.unknownRuns, retryTaskId: wholeChapter ? `roster:${chapter.id}` : taskId, onPoolReady: ready })
      })
      // Wait for cross-tab ownership and pool installation, not model completion.
      await Promise.race([poolReady, completion])
      return { completion }
    }
  } catch (reason) {
    if (!controller) {
      batchStopRequests.delete(bookId)
      publishBatch(bookId, { running: false, stopRequested: false, finishedAt: Date.now() })
    }
    throw reason
  }
}

export function BatchRetryControls({ bookId, taskId }: { bookId: string; taskId?: string }) {
  const progress = useBatchProgress(bookId)
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const tasks = progress.tasks.filter(task => task.state === 'failed' && task.retryable && (!taskId || task.id === taskId))
  if (!tasks.length) return null
  return <div className="ndr-form-actions">
    {tasks.map(task => <button key={task.id} type="button" disabled={busy[task.id] || progress.stopRequested}
      title={busy[task.id] ? '本窗口正在准备重试，请等待加入队列。' : progress.stopRequested ? '队列正在停止，请等待请求收尾后再重试。' : '加入重试队列，有空闲并发位置时立即处理。'}
      onClick={() => { setBusy(current => ({ ...current, [task.id]: true })); setError(null); void retryBatchTask(bookId, task.id)
        .catch(reason => setError(reason instanceof Error ? reason.message : '重试失败'))
        .finally(() => setBusy(current => ({ ...current, [task.id]: false }))) }}>
      {busy[task.id] ? '准备重试…' : `重试${task.type === 'roster' ? '人物识别' : task.windowLabel}`}{!taskId && `（${task.chapterTitle}）`}
    </button>)}
    {error && <p className="status-error" role="alert">{error}</p>}
    {!taskId && <p className="hint">重试会使用原模型配置和剩余额度，不刷新已用额度。人物识别重试成功后继续本章未完成窗口；成功窗口不会重复处理。</p>}
  </div>
}

function publishBatch(bookId: string, update: Partial<BatchProgressSnapshot>) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  batchSnapshots.set(bookId, { ...current, ...update, revision: current.revision + 1 })
  persistBatch(bookId)
  batchListeners.forEach((listener) => listener())
}

function updateBatchTask(
  bookId: string,
  taskId: string,
  state: BatchTaskState,
  error: string | null = null,
) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  const tasks = current.tasks.map((task) => task.id === taskId ? { ...task, state, error, ...(state === 'running' ? { jobId: null } : {}) } : task)
  const chapterId = tasks.find(task => task.id === taskId)?.chapterId
  publishBatch(bookId, {
    tasks,
    ...(chapterId && current.chapterStates[chapterId] ? { chapterStates: { ...current.chapterStates,
      [chapterId]: { ...current.chapterStates[chapterId], pendingTasks: tasks.filter(task => task.chapterId === chapterId && ['queued', 'running'].includes(task.state)).length },
    } } : {}),
  })
}

function updateChapterProgress(
  bookId: string,
  chapterId: string,
  update: Partial<ChapterProcessingProgress>,
) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  const previous = current.chapterStates[chapterId] ?? {
    state: 'unprocessed' as const,
    completedWindows: 0,
    totalWindows: 0,
    error: null,
  }
  publishBatch(bookId, {
    chapterStates: {
      ...current.chapterStates,
      [chapterId]: { ...previous, ...update },
    },
  })
}

function recordCompletedWindow(bookId: string, chapterId: string) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  const previous = current.chapterStates[chapterId]
  if (!previous) return
  publishBatch(bookId, {
    chapterStates: {
      ...current.chapterStates,
      [chapterId]: {
        ...previous,
        completedWindows: Math.min(previous.totalWindows, previous.completedWindows + 1),
      },
    },
    annotationRevisions: {
      ...current.annotationRevisions,
      [chapterId]: (current.annotationRevisions[chapterId] ?? 0) + 1,
    },
  })
}

function batchShouldStop(bookId: string, localStop: boolean): boolean {
  return localStop || batchStopRequests.has(bookId)
}

export function isBatchRunning(bookId: string): boolean {
  return batchSnapshots.get(bookId)?.running ?? false
}

export function hasBatchWork(): boolean {
  return getAdmissions().some(item => ['queued', 'running'].includes(item.phase)) || hasSingleWork() || retryPlanning.size > 0 || [...batchSnapshots.values()].some(batch => batch.running)
}

export function clearBatchProgress(bookId: string) {
  batchSnapshots.delete(bookId)
  batchStopRequests.delete(bookId)
  batchExecutions.delete(bookId)
  chapterStopRequests.delete(bookId)
  batchRequests.delete(bookId)
  accountedJobs.delete(bookId)
  removeJournal(`batch:${bookId}`)
  batchListeners.forEach((listener) => listener())
}

export function requestBatchStop(bookId: string) {
  retryStopGenerations.set(bookId, (retryStopGenerations.get(bookId) ?? 0) + 1)
  batchStopRequests.add(bookId)
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  publishBatch(bookId, {
    stopRequested: true,
    message: '正在安全停止：不会再派发新任务，等待在途请求收尾…',
    tasks: current.tasks.map((task) => task.state === 'queued'
      ? { ...task, state: 'cancelled', error: null }
      : task),
    chapterStates: Object.fromEntries(Object.entries(current.chapterStates).map(([chapterId, progress]) => [
      chapterId,
      { ...progress, pendingTasks: current.tasks.filter(task => task.chapterId === chapterId && task.state === 'running').length,
        ...(progress.state === 'queued' ? { state: 'stopped' as const } : {}) },
    ])),
  })
}

export function useBatchProgress(bookId: string | undefined): BatchProgressSnapshot {
  useEffect(() => { if (bookId && !batchSnapshots.has(bookId)) void restoreBatchProcessing(bookId) }, [bookId])
  return useSyncExternalStore(
    subscribeBatch,
    () => (bookId ? batchSnapshots.get(bookId) ?? EMPTY_BATCH : EMPTY_BATCH),
  )
}

const BATCH_JOURNAL_PREFIX = 'ndr:tasks:v1:batch:'

/** Observe another tab; never copy its execution state or acquire its lock. */
function receiveBatchSnapshot(key: string, raw: string | null) {
  const bookId = key.slice(BATCH_JOURNAL_PREFIX.length)
  if (!bookId) return
  if (raw === null) {
    batchSnapshots.delete(bookId)
  } else {
    try {
      const saved = JSON.parse(raw) as SavedBatch
      const next = saved.snapshot
      if (saved.schema !== 1 || saved.execution?.bookId !== bookId || !next
        || !Number.isFinite(next.startedAt) || !Number.isFinite(next.revision)
        || !next.chapterStates || !next.annotationRevisions || !Array.isArray(next.tasks)) return
      const current = batchSnapshots.get(bookId)
      if (current && (next.startedAt < current.startedAt
        || next.startedAt === current.startedAt && next.revision <= current.revision)) return
      batchSnapshots.set(bookId, next)
    } catch { return } // Do not dispatch work from damaged foreign records.
  }
  batchListeners.forEach(listener => listener())
}

function receiveBatchStorage(event: StorageEvent) {
  if (event.storageArea && event.storageArea !== localStorage) return
  if (event.key?.startsWith(BATCH_JOURNAL_PREFIX)) receiveBatchSnapshot(event.key, event.newValue)
}

function subscribeBatch(listener: () => void) {
  if (batchListeners.size === 0) {
    window.addEventListener('storage', receiveBatchStorage)
    // Catch up after a period with no mounted progress consumers.
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(BATCH_JOURNAL_PREFIX)) receiveBatchSnapshot(key, localStorage.getItem(key))
    }
  }
  batchListeners.add(listener)
  return () => {
    batchListeners.delete(listener)
    if (batchListeners.size === 0) window.removeEventListener('storage', receiveBatchStorage)
  }
}

export function useBatchChapterProgress(bookId: string | undefined) {
  return useSyncExternalStore(
    subscribeBatch,
    () => (bookId ? batchSnapshots.get(bookId)?.chapterStates ?? EMPTY_BATCH.chapterStates : EMPTY_BATCH.chapterStates),
  )
}

export function useBatchAnnotationRevisions(bookId: string | undefined) {
  return useSyncExternalStore(
    subscribeBatch,
    () => (bookId ? batchSnapshots.get(bookId)?.annotationRevisions ?? EMPTY_BATCH.annotationRevisions : EMPTY_BATCH.annotationRevisions),
  )
}

export function useBatchCatalogRevision(bookId: string | undefined) {
  return useSyncExternalStore(
    subscribeBatch,
    () => (bookId ? batchSnapshots.get(bookId)?.catalogRevision ?? 0 : 0),
  )
}

export function useBatchMessage(bookId: string | undefined) {
  return useSyncExternalStore(
    subscribeBatch,
    () => (bookId ? batchSnapshots.get(bookId)?.message ?? '' : ''),
  )
}

function positiveIntegerOrNull(value: string): number | null {
  if (value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : null
}

function nonNegativeInteger(value: string): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.max(0, Math.floor(parsed)) : 0
}

function jobTokens(job: JobDetailOut): number {
  const value = job.usage?.total_tokens
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value
  const input = job.usage?.input_tokens
  const output = job.usage?.output_tokens
  return [input, output].reduce<number>((sum, tokens) =>
    sum + (typeof tokens === 'number' && Number.isFinite(tokens) && tokens >= 0 ? tokens : 0), 0)
}

async function waitForJob(job: JobDetailOut): Promise<JobDetailOut> {
  // Stopping prevents dispatch; keep the slot until the submitted request really finishes.
  return waitForJobCompletion(job, () => undefined)
}

async function waitForChapterJob(bookId: string, plan: ChapterPlan, taskId: string, submitted: Promise<JobDetailOut>) {
  const job = await submitted
  publishBatch(bookId, { tasks: (batchSnapshots.get(bookId)?.tasks ?? []).map(task => task.id === taskId ? { ...task, jobId: job.id } : task) })
  if (plan.cancelled || chapterShouldStop(bookId, plan.chapter.id)) {
    try { await pauseJob(job.id) } catch (reason) {
      updateChapterProgress(bookId, plan.chapter.id, { error: `暂停请求失败，当前请求将继续收尾：${reason instanceof Error ? reason.message : String(reason)}` })
    }
  }
  return waitForJob(job)
}

export interface ChapterPlan {
  chapter: ChapterOut
  estimate: EstimateOut
  completedWindows?: number
  totalWindows?: number
  reuseRoster?: boolean
  cancelled?: boolean
}

interface BatchProcessorProps {
  bookId: string
  bookVersionId: string | null | undefined
  chapters: ChapterOut[]
  profiles: ModelProfileOut[]
  onFinished: () => void
  showConfiguration?: boolean
  initialChapterId?: string | null
}

export interface BatchExecution {
  queueId?: string
  chapterFilter?: ChapterFilter
  restoring?: boolean
  bookId: string
  bookVersionId: string
  requested: ChapterOut[]
  plans: ChapterPlan[]
  preferences: ProcessingPreferences
  forceReprocess?: boolean
  initialSpent?: number
  initialTotalSpent?: number
  initialUnknownRuns?: number
  onUsage?: (spent: number) => void
  onProgress?: (message: string) => void
  onError?: (message: string | null) => void
  onTokenLimit?: (value: string) => void
  onFinished?: () => void
  retryTaskId?: string
  expandable?: boolean
  onPoolReady?: () => void
}

/** Shared sequenced roster/window pipeline for manual batches and reader look-ahead. */
export async function runBatchProcessing(execution: BatchExecution) {
  const reason = dialogueStrategyDisabledReason(execution.preferences.dialogueStrategy, execution.preferences.maxRecheckRounds)
  if (reason) throw new Error(reason)
  const chapterFilter = normalizeChapterFilter(execution.chapterFilter ?? getGeneralSettings())
  return withWorkflowLock(`processing:${execution.bookId}`, async () => {
    if (execution.queueId && getAdmissions().find(item => item.id === execution.queueId)?.stopRequested) return
    if (execution.queueId && (await fetchBook(execution.bookId)).active_version_id !== execution.bookVersionId) throw new Error('书籍版本已改变，未派发旧范围。')
    return runBatchInternal({ ...execution, chapterFilter })
  })
}

export function automaticAllowance(bookId: string) {
  const saved = batchExecutions.get(bookId)
  return saved?.execution.expandable ? saved.spent : null
}

export function refreshAutomaticAllowance(bookId: string) {
  const saved = batchExecutions.get(bookId)
  if (saved?.execution.expandable) { saved.spent = 0; persistBatch(bookId) }
}

async function runBatchInternal({ bookId, bookVersionId, requested, plans, preferences,
  queueId,
  forceReprocess = false, initialSpent = 0, initialTotalSpent = initialSpent, initialUnknownRuns = 0,
  onUsage, onProgress = () => undefined,
  onError = () => undefined, onTokenLimit = () => undefined, onFinished = () => undefined, retryTaskId,
  expandable = false, restoring = false, chapterFilter = defaultChapterFilter(), onPoolReady = () => undefined }: BatchExecution) {
  if (isBatchRunning(bookId) && !restoring) throw new Error('本书已有批量任务运行，请先等待或停止。')
  const restoredSnapshot = restoring ? batchSnapshots.get(bookId) : undefined
  if (!restoring) { batchRequests.set(bookId, {}); accountedJobs.set(bookId, new Set()) }
  const { profileId, concurrency, maxRecheckRounds, maxFormatRetries } = preferences
  if (!profileId) throw new Error('请选择模型配置。')
  const options = inferenceOptions(preferences)
  requested = [...requested]
  plans = [...plans]
  const execution: BatchExecution = { queueId, bookId, bookVersionId, requested, plans, preferences: { ...preferences },
    forceReprocess, onUsage, onProgress, onError, onTokenLimit, onFinished, expandable, chapterFilter }
  const savedExecution = { execution, spent: initialSpent, totalSpent: initialTotalSpent, unknownRuns: initialUnknownRuns }
  batchExecutions.set(bookId, savedExecution)
  const tokenLimitText = preferences.tokenLimit === null ? '' : String(preferences.tokenLimit)
  const setProgress = onProgress
  const setError = onError
  const setTokenLimitText = onTokenLimit
  const stopRef = { current: false }
    const selectedPlans: ChapterPlan[] = plans.filter(({ chapter }) => !chapterFilterReason(chapter.title, chapterFilter)
      && (forceReprocess || !chapter.dialogue_processed)).map(plan => ({ ...plan,
      cancelled: plan.cancelled || Boolean(restoredSnapshot?.stopRequested) || restoredSnapshot?.chapterStates[plan.chapter.id]?.state === 'stopped' || restoredSnapshot?.chapterStates[plan.chapter.id]?.cancelRequested }))
    selectedPlans.forEach(plan => chapterStopRequests.get(bookId)?.delete(plan.chapter.id))
    let tokenLimit = positiveIntegerOrNull(tokenLimitText)
    let spent = initialSpent
    let totalSpent = initialTotalSpent
    let unknownRuns = initialUnknownRuns
    const usageLabel = () => unknownRuns > 0
      ? `本次已知累计 ${totalSpent.toLocaleString()} tokens（另有 ${unknownRuns} 次调用用量未知，未计入）`
      : `本次累计 ${totalSpent.toLocaleString()} tokens`
    let reserved = 0
    let warnedAtEightyPercent = false
    let warningOpen = false
    const limiter = createTaskLimiter(concurrency)
    const pendingWork: Promise<void>[] = []
    const directRetryPlans: ChapterPlan[] = []
    const inFlight = new Set<Promise<void>>()
    let wakeQueue: (() => void) | null = null
    let abortReason: BatchAbortError | null = null
    let nextRosterIndex = 0
    const trackWork = (promise: Promise<void>) => {
      pendingWork.push(promise)
      inFlight.add(promise)
      void promise.then(() => {
        inFlight.delete(promise); wakeQueue?.()
      }, reason => {
        inFlight.delete(promise)
        if (isBatchAbortError(reason)) { stopRef.current = true; abortReason = reason }
        wakeQueue?.()
      })
      return promise
    }
    const failedChapterIds = new Set<string>()
    const completingChapters = new Set<string>()
    const skippedEmptyChapterIds = new Set<string>()
    const cancelledChapterIds = new Set<string>(selectedPlans.filter(plan => plan.cancelled).map(plan => plan.chapter.id))
    stopRef.current = false
    if (restoredSnapshot?.stopRequested) batchStopRequests.add(bookId)
    else batchStopRequests.delete(bookId)
    setError(null)
    const planTasks = ({ chapter, estimate, reuseRoster }: ChapterPlan): BatchTaskProgress[] => {
      const chapterTitle = chapter.title || `第 ${chapter.ordinal + 1} 章`
      return [
        {
          id: `roster:${chapter.id}`,
          type: 'roster' as const,
          chapterId: chapter.id,
          chapterTitle,
          windowId: null,
          windowLabel: '整章人物识别',
          state: reuseRoster || retryTaskId?.startsWith('dialogue:') ? 'completed' as const : 'queued' as const,
          error: null,
        },
        ...(estimate.windows ?? []).map((window) => ({
          id: `dialogue:${chapter.id}:${String(window.window_id)}`,
          type: 'dialogue' as const,
          chapterId: chapter.id,
          chapterTitle,
          windowId: String(window.window_id),
          windowLabel: `窗口 ${String(window.ordinal)} · ${String(window.target_count)} 句对白`,
          state: 'queued' as const,
          error: null,
        })),
      ]
    }
    const tasks = selectedPlans.flatMap(planTasks)
    publishBatch(bookId, {
      running: true,
      startedAt: restoredSnapshot?.startedAt ?? Date.now(),
      finishedAt: null,
      stopRequested: restoredSnapshot?.stopRequested ?? false,
      message: '准备批量处理…',
      tasks: restoredSnapshot ? [...restoredSnapshot.tasks.filter(old => !tasks.some(task => task.id === old.id)),
        ...tasks.map(task => restoredSnapshot.tasks.find(old => old.id === task.id) ?? task)] : retryTaskId ? [...(batchSnapshots.get(bookId)?.tasks ?? []).filter(old => !tasks.some(task => task.id === old.id)), ...tasks] : tasks,
      chapterStates: { ...(retryTaskId ? batchSnapshots.get(bookId)?.chapterStates : {}), ...Object.fromEntries(requested.map((chapter) => {
        const plan = selectedPlans.find((item) => item.chapter.id === chapter.id)
        return [chapter.id, {
          state: plan ? 'queued' : chapterFilterReason(chapter.title, chapterFilter) ? 'skipped' : chapter.dialogue_processed ? 'processed' : 'unprocessed',
          completedWindows: restoredSnapshot?.chapterStates[chapter.id]?.completedWindows ?? plan?.completedWindows ?? 0,
          totalWindows: plan?.totalWindows ?? plan?.estimate.windows?.length ?? 0,
          pendingTasks: plan ? (plan.estimate.windows?.length ?? 0) + (plan.reuseRoster ? 0 : 1) : 0,
          error: chapterFilterReason(chapter.title, chapterFilter),
          manualStatusCleared: true,
          ...restoredSnapshot?.chapterStates[chapter.id],
        } satisfies ChapterProcessingProgress]
      })) },
    })

    expandableBatches.set(bookId, { versionId: bookVersionId, automatic: expandable && !retryTaskId,
      cancel: chapterId => {
        [...selectedPlans, ...directRetryPlans].filter(plan => plan.chapter.id === chapterId).forEach(plan => { plan.cancelled = true })
        cancelledChapterIds.add(chapterId)
        failedChapterIds.delete(chapterId)
        wakeQueue?.()
      },
      retry: plan => {
        const requests = batchRequests.get(bookId) ?? {}
        delete requests[`roster:${plan.chapter.id}`]
        for (const window of plan.estimate.windows ?? []) delete requests[`dialogue:${plan.chapter.id}:${String(window.window_id)}`]
        chapterStopRequests.get(bookId)?.delete(plan.chapter.id)
        failedChapterIds.delete(plan.chapter.id)
        cancelledChapterIds.delete(plan.chapter.id)
        skippedEmptyChapterIds.delete(plan.chapter.id)
        plan.cancelled = false
        if (plan.reuseRoster) directRetryPlans.push(plan)
        else selectedPlans.push(plan)
        const oldPlan = plans.findIndex(old => old.chapter.id === plan.chapter.id)
        if (oldPlan >= 0) {
          const windows = [...(plans[oldPlan].estimate.windows ?? []).filter(old =>
            !(plan.estimate.windows ?? []).some(window => String(window.window_id) === String(old.window_id))), ...(plan.estimate.windows ?? [])]
          plans[oldPlan] = { ...plan, estimate: { ...plan.estimate, windows } }
        }
        else plans.push(plan)
        if (!requested.some(chapter => chapter.id === plan.chapter.id)) requested.push(plan.chapter)
        const tail = selectedPlans.splice(nextRosterIndex).sort((a, b) => a.chapter.ordinal - b.chapter.ordinal)
        selectedPlans.push(...tail)
        const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
        const tasks = planTasks(plan)
        const mergedTasks = [...current.tasks.filter(task => !tasks.some(replacement => replacement.id === task.id)), ...tasks]
        publishBatch(bookId, {
          tasks: mergedTasks,
          chapterStates: { ...current.chapterStates, [plan.chapter.id]: { state: mergedTasks.some(task => task.chapterId === plan.chapter.id && task.state === 'running')
            ? current.chapterStates[plan.chapter.id]?.state === 'roster' ? 'roster' : 'dialogue' : 'queued',
            completedWindows: Math.max(current.chapterStates[plan.chapter.id]?.completedWindows ?? 0, plan.completedWindows ?? 0), totalWindows: plan.totalWindows ?? plan.estimate.windows?.length ?? 0,
            error: null, cancelRequested: false, manualStatusCleared: true, pendingTasks: mergedTasks.filter(task => task.chapterId === plan.chapter.id && ['queued', 'running'].includes(task.state)).length } }, message: '已将未完成任务加入重试队列，其他任务继续处理。',
        })
        if (plan.reuseRoster) trackWork(scheduleDialogue(plan, requested.findIndex(chapter => chapter.id === plan.chapter.id)))
        wakeQueue?.()
      }, append: additions => {
      if (batchShouldStop(bookId, stopRef.current)) return 0
      const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
      const runningChapters = new Set(current.tasks.filter(task => task.state === 'running'
        || task.state === 'failed' && !task.retryable).map(task => task.chapterId))
      const seen = new Set(selectedPlans.filter(plan => !plan.cancelled || runningChapters.has(plan.chapter.id)).map(plan => plan.chapter.id))
      const fresh = additions.filter(plan => {
        if (chapterFilterReason(plan.chapter.title, chapterFilter) || plan.chapter.dialogue_processed || seen.has(plan.chapter.id)) return false
        seen.add(plan.chapter.id)
        return true
      })
      if (!fresh.length) return 0
      for (const plan of fresh) {
        const requests = batchRequests.get(bookId) ?? {}
        delete requests[`roster:${plan.chapter.id}`]
        for (const window of plan.estimate.windows ?? []) delete requests[`dialogue:${plan.chapter.id}:${String(window.window_id)}`]
        chapterStopRequests.get(bookId)?.delete(plan.chapter.id)
        cancelledChapterIds.delete(plan.chapter.id)
        failedChapterIds.delete(plan.chapter.id)
        skippedEmptyChapterIds.delete(plan.chapter.id)
      }
      selectedPlans.push(...fresh)
      // Never reorder a roster already dispatched; sort only the not-yet-started tail.
      const tail = selectedPlans.splice(nextRosterIndex).sort((a, b) => a.chapter.ordinal - b.chapter.ordinal)
      selectedPlans.push(...tail)
      requested.push(...fresh.map(plan => plan.chapter).filter(chapter => !requested.some(old => old.id === chapter.id)))
      plans.push(...fresh)
      const estimated = fresh.reduce((total, plan) => total + plan.estimate.total_tokens
        + estimateRosterTokens(plan.chapter.end_cp - plan.chapter.start_cp, preferences), 0)
      const message = `已加入 ${fresh.length} 章，预计约 ${estimated.toLocaleString()} Tokens；有空闲位置时继续处理。`
      publishBatch(bookId, {
        tasks: [...current.tasks.filter(task => !fresh.some(plan => plan.chapter.id === task.chapterId)), ...fresh.flatMap(planTasks)],
        chapterStates: { ...current.chapterStates, ...Object.fromEntries(fresh.map(({ chapter, estimate }) => [chapter.id, {
          state: 'queued' as const, completedWindows: 0, totalWindows: estimate.windows?.length ?? 0, pendingTasks: (estimate.windows?.length ?? 0) + 1, error: null, cancelRequested: false, manualStatusCleared: true,
        }])) }, message,
      })
      setProgress(message)
      wakeQueue?.()
      return fresh.length
    } })
    onPoolReady()

    const checkBudgetReminder = () => {
      if (tokenLimit === null || warnedAtEightyPercent || warningOpen || spent < Math.ceil(tokenLimit * 0.8)) return
      warnedAtEightyPercent = true
      warningOpen = true
      const answer = window.prompt(
        `本次已使用约 ${spent.toLocaleString()} tokens，达到上限 ${tokenLimit.toLocaleString()} 的 80%。\n\n` +
          '请输入新的 Token 上限并直接继续；如果额度已经刷新，请输入 0，系统会按原上限从 0 重新计算。取消则安全停止。',
        String(tokenLimit),
      )
      warningOpen = false
      if (answer === null) throw new BatchAbortError('已在额度接近上限时停止')
      const nextLimit = Number(answer.trim())
      if (nextLimit === 0) {
        spent = 0
        savedExecution.spent = 0
        onUsage?.(0)
        warnedAtEightyPercent = false
        publishBatch(bookId, { message: '额度已刷新，继续后台处理…' })
        return
      }
      if (!Number.isSafeInteger(nextLimit) || nextLimit <= spent + reserved) {
        throw new BatchAbortError(`新额度必须大于已使用及在途预留的 ${(spent + reserved).toLocaleString()} tokens`)
      }
      tokenLimit = Math.floor(nextLimit)
      execution.preferences.tokenLimit = tokenLimit
      setTokenLimitText(String(tokenLimit))
      warnedAtEightyPercent = spent >= Math.ceil(tokenLimit * 0.8)
      publishBatch(bookId, { message: `Token 上限已调整为 ${tokenLimit.toLocaleString()}，继续后台处理…` })
    }

    const runMetered = <T,>(reserveEstimate: number, task: (available: number | null) => Promise<T>, shouldSkip: () => boolean = () => false, taskId?: string) =>
      limiter.run(() => inSharedTaskPool(preferences.concurrency, async () => {
        if (shouldSkip()) return
        if (batchShouldStop(bookId, stopRef.current)) throw new Error('批量处理已停止')
        const existing = Boolean(restoring && taskId && batchRequests.get(bookId)?.[taskId]?.jobId)
        if (!existing) checkBudgetReminder()
        const available = tokenLimit === null ? null : tokenLimit - spent - reserved
        if (!existing && available !== null && available <= 0) {
          throw new BatchAbortError('已达到本次 Token 使用上限')
        }
        // 用启动前的本地估算预留额度；所有在途任务的预留之和不会超过
        // 当前批次剩余额度，完成后再按提供方返回的实测用量结算。
        const reservation = existing ? 0 : available === null
          ? 0
          : Math.min(Math.max(1, reserveEstimate), available)
        if (!existing) reserved += reservation
        try {
          return await task(available === null ? null : reservation)
        } finally {
          reserved = Math.max(0, reserved - reservation)
        }
      }))

    const recordUsage = (job: JobDetailOut, stage: string) => {
      const accounted = accountedJobs.get(bookId) ?? new Set<string>()
      accountedJobs.set(bookId, accounted)
      if (restoring && accounted.has(job.id)) return
      accounted.add(job.id)
      const tokens = jobTokens(job)
      const reportedUnknown = job.usage?.unknown_runs
      const jobUnknown = Math.max(job.unknown_usage_runs ?? 0,
        typeof reportedUnknown === 'number' && Number.isFinite(reportedUnknown) ? reportedUnknown : 0)
      spent += tokens
      totalSpent += tokens
      unknownRuns += jobUnknown
      savedExecution.spent = spent
      savedExecution.totalSpent = totalSpent
      savedExecution.unknownRuns = unknownRuns
      persistBatch(bookId)
      onUsage?.(spent)
      if (tokenLimit !== null && jobUnknown > 0) {
        stopRef.current = true
        throw new BatchAbortError(`模型没有返回${stage}用量，无法可靠执行 Token 上限，批量处理已停止`)
      }
      try { checkBudgetReminder() } catch (reason) {
        if (isBatchAbortError(reason)) stopRef.current = true
        throw reason
      }
    }

    const markChapterTaskFailure = (
      chapterId: string,
      taskId: string,
      state: BatchTaskState,
      error: string | null,
    ) => {
      updateBatchTask(bookId, taskId, state, error)
      if (state !== 'failed') return
      failedChapterIds.add(chapterId)
      updateChapterProgress(bookId, chapterId, { state: 'failed', error })
      if (!taskId.startsWith('roster:')) return
      const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
      publishBatch(bookId, {
        tasks: current.tasks.map((task) => task.chapterId === chapterId && task.state === 'queued'
          ? { ...task, state: 'cancelled' as const, error: null }
          : task),
      })
      updateChapterProgress(bookId, chapterId, { pendingTasks: 0 })
    }

    const scheduleRoster = async (plan: ChapterPlan, index: number) => {
      if (plan.cancelled) return
      if (plan.chapter.processing_status_override != null) {
        plan.chapter = await setChapterProcessingStatus(bookId, plan.chapter.id, bookVersionId, null)
        const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
        publishBatch(bookId, { catalogRevision: current.catalogRevision + 1 })
      }
      const { chapter } = plan
      const taskId = `roster:${chapter.id}`
      if (plan.cancelled) return Promise.resolve()
      if (restoring && restoredSnapshot?.tasks.find(task => task.id === taskId)?.state === 'completed') {
        const existing = await fetchCharacterRoster(bookId, chapter.id, bookVersionId)
        if (existing.status === 'CONFIRMED') return
      }
      if (plan.reuseRoster || retryTaskId?.startsWith('dialogue:')) return fetchCharacterRoster(bookId, chapter.id, bookVersionId).then(roster => {
        if (plan.cancelled) return
        if (roster.status !== 'CONFIRMED') throw new Error('请先确认本章人物，再重试对白窗口。')
        updateChapterProgress(bookId, chapter.id, { state: 'dialogue', error: null })
      })
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
      return runMetered(estimateRosterTokens(chapter.end_cp - chapter.start_cp, preferences), async (available) => {
        if (plan.cancelled) return
        updateChapterProgress(bookId, chapter.id, { state: 'roster', error: null })
        publishBatch(bookId, { message: `${prefix}：正在识别人物…` })
        setProgress(`${prefix}：正在识别人物…`)
        updateBatchTask(bookId, taskId, 'running')
        let retryable = false
        try {
          const rosterJob = await waitForChapterJob(bookId, plan, taskId,
            submitBatchRequest(bookId, taskId, 'roster', {
              ...rosterRepairOptions(preferences),
              bookVersionId,
              profileId,
              inferenceOptions: options,
              maxInputTokens: available,
              idempotencyKey: freshIdempotencyKey('batch-roster', `${bookId}:${chapter.id}:${profileId}`),
              allowOverwriteManual: getGeneralSettings().allowOverwriteManualCharacters,
            }),
          )
          retryable = (rosterJob.state === 'FAILED' || rosterJob.state === 'COMPLETED')
            && !(tokenLimit !== null && rosterJob.unknown_usage_runs > 0)
          recordUsage(rosterJob, '人物识别')
          if (plan.cancelled) {
            if (rosterJob.state === 'NEEDS_RECONCILIATION') throw new Error(rosterJob.last_error || '请求结果不明确，请先查看任务详情。')
            updateBatchTask(bookId, taskId, rosterJob.state === 'COMPLETED' ? 'completed' : 'cancelled')
            finishChapterCancellation(bookId, chapter.id)
            return
          }
          if (rosterJob.state !== 'COMPLETED') throw new Error(rosterJob.last_error || `${prefix}的人物识别未完成`)
          const roster = await fetchCharacterRoster(bookId, chapter.id, bookVersionId)
          const accepted = (roster.candidates ?? []).filter((candidate) => Boolean(candidate.canonical_name))
          if (accepted.length === 0) {
            skippedEmptyChapterIds.add(chapter.id)
            updateBatchTask(bookId, taskId, 'completed')
            const noText = rosterJob.progress?.skipped_reason === 'no_text'
            updateChapterProgress(bookId, chapter.id, {
              state: noText ? 'processed' : 'stopped',
              error: noText ? null : '未识别到人物，已跳过本章对白处理', pendingTasks: 0,
            })
            const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
            publishBatch(bookId, {
              message: `${prefix}：${noText ? '没有正文文字，已完成（未调用模型）' : '未识别到人物，跳过本章'}，继续下一章`,
              catalogRevision: current.catalogRevision + (noText ? 1 : 0),
              tasks: current.tasks.map((task) => task.chapterId === chapter.id && task.state === 'queued'
                ? { ...task, state: 'cancelled' as const, error: null }
                : task),
            })
            return
          }
          const pov = accepted.find((candidate) => candidate.pov_candidate) ?? accepted[0]
          if (!restoring || roster.status !== 'CONFIRMED') await confirmCharacterRoster(bookId, chapter.id, {
            bookVersionId,
            confirmationMode: 'automatic',
            candidates: accepted.map((candidate) => ({
              temp_ref: candidate.temp_ref,
              accepted: true,
              character_id: candidate.character_id,
              canonical_name: candidate.canonical_name,
              aliases: candidate.aliases,
              description: candidate.description,
            })),
            povTempRef: pov.temp_ref,
            expectedVersion: roster.version,
          })
          if (plan.cancelled) {
            updateBatchTask(bookId, taskId, 'cancelled'); finishChapterCancellation(bookId, chapter.id); return
          }
          updateBatchTask(bookId, taskId, 'completed')
          updateChapterProgress(bookId, chapter.id, { state: 'dialogue', error: null })
          publishBatch(bookId, { message: `${prefix}：人物已确认，正在并发处理对白窗口…` })
        } catch (reason) {
          if (plan.cancelled && !isBatchAbortError(reason)) {
            updateBatchTask(bookId, taskId, 'failed', reason instanceof Error ? reason.message : '请求结果不明确')
            publishBatch(bookId, { tasks: (batchSnapshots.get(bookId)?.tasks ?? []).map(task => task.id === taskId ? { ...task, retryable } : task) })
            finishChapterCancellation(bookId, chapter.id); return
          }
          const message = reason instanceof Error ? reason.message : '人物识别失败'
          const stopped = batchShouldStop(bookId, stopRef.current)
          markChapterTaskFailure(chapter.id, taskId, stopped ? 'cancelled' : 'failed', message)
          publishBatch(bookId, { tasks: (batchSnapshots.get(bookId)?.tasks ?? []).map(task => task.id === taskId ? { ...task, retryable } : task) })
          if (stopped) throw new BatchAbortError('批量处理已停止')
          if (isBatchAbortError(reason)) throw reason
        }
      }, () => Boolean(plan.cancelled), taskId)
    }

    const scheduleDialogue = (plan: ChapterPlan, index: number) => {
      const { chapter, estimate } = plan
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
      let chapterFailed = false
      const jobs = (estimate.windows ?? []).map((window) => {
        const windowId = String(window.window_id)
        const taskId = `dialogue:${chapter.id}:${windowId}`
        return runMetered(Number(window.estimated_tokens) || 1, async (available) => {
          if (plan.cancelled) return
          updateBatchTask(bookId, taskId, 'running')
          updateChapterProgress(bookId, chapter.id, { state: 'dialogue', error: null })
          let retryable = false
          try {
            const dialogueJob = await waitForChapterJob(bookId, plan, taskId,
              submitBatchRequest(bookId, taskId, 'dialogue', {
                bookId,
                mode: 'process',
                bookVersionId,
                range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp, dialogueStrategy: preferences.dialogueStrategy },
                selectedWindowIds: [windowId],
                forceReprocess,
                profileId,
                inferenceOptions: options,
                readingMode: 'reread',
                visibleHorizonCp: null,
                budget: { maxInputTokens: available, maxOutputTokens: available, maxRecheckRounds, maxFormatRetries,
                  maxRechecks: (preferences as ProcessingPreferences & { maxRechecks?: number }).maxRechecks },
                idempotencyKey: freshIdempotencyKey(
                  'batch-dialogue-window',
                  `${bookId}:${chapter.id}:${windowId}:${profileId}:${maxRecheckRounds}`,
                ),
              }),
            )
            retryable = dialogueJob.state === 'FAILED' && !(tokenLimit !== null && dialogueJob.unknown_usage_runs > 0)
            recordUsage(dialogueJob, '对白处理')
            if (plan.cancelled) {
              if (dialogueJob.state === 'NEEDS_RECONCILIATION') throw new Error(dialogueJob.last_error || '请求结果不明确，请先查看任务详情。')
              updateBatchTask(bookId, taskId, dialogueJob.state === 'COMPLETED' ? 'completed' : 'cancelled')
              if (dialogueJob.state === 'COMPLETED') recordCompletedWindow(bookId, chapter.id)
              finishChapterCancellation(bookId, chapter.id)
              return
            }
            if (dialogueJob.state !== 'COMPLETED') throw new Error(dialogueJob.last_error || `${prefix}的窗口 ${windowId} 未完成`)
            updateBatchTask(bookId, taskId, 'completed')
            if (!restoring || restoredSnapshot?.tasks.find(task => task.id === taskId)?.state !== 'completed') recordCompletedWindow(bookId, chapter.id)
          } catch (reason) {
            if (plan.cancelled && !isBatchAbortError(reason)) {
              updateBatchTask(bookId, taskId, 'failed', reason instanceof Error ? reason.message : '请求结果不明确')
              publishBatch(bookId, { tasks: (batchSnapshots.get(bookId)?.tasks ?? []).map(task => task.id === taskId ? { ...task, retryable } : task) })
              finishChapterCancellation(bookId, chapter.id); return
            }
            const message = reason instanceof Error ? reason.message : '对白窗口处理失败'
            const stopped = batchShouldStop(bookId, stopRef.current)
            markChapterTaskFailure(chapter.id, taskId, stopped ? 'cancelled' : 'failed', message)
            publishBatch(bookId, { tasks: (batchSnapshots.get(bookId)?.tasks ?? []).map(task => task.id === taskId ? { ...task, retryable } : task) })
            chapterFailed = true
            if (stopped) throw new BatchAbortError('批量处理已停止')
            if (isBatchAbortError(reason)) throw reason
          }
        }, () => Boolean(plan.cancelled), taskId)
      })
      return Promise.all(jobs).then(async () => {
        if (plan.cancelled || chapterFailed) return
        const remaining = (batchSnapshots.get(bookId)?.tasks ?? []).filter(task => task.chapterId === chapter.id && task.state !== 'completed')
        if (remaining.length) {
          if (remaining.some(task => task.state === 'failed')) failedChapterIds.add(chapter.id)
          const pending = remaining.some(task => ['queued', 'running'].includes(task.state))
          updateChapterProgress(bookId, chapter.id, { state: pending ? 'dialogue' : remaining.some(task => task.state === 'failed') ? 'failed' : 'stopped',
            error: pending ? null : '本窗口已保存，其他窗口尚未完成。' })
          return
        }
        failedChapterIds.delete(chapter.id)
        if (completingChapters.has(chapter.id)) return
        completingChapters.add(chapter.id)
        try {
          await completeChapterProcessing(bookId, chapter.id, bookVersionId)
        } catch (reason) {
          if (retryTaskId && reason instanceof ApiError && reason.status === 409 && reason.code === 'RESOURCE_CONFLICT') {
            const remaining = (batchSnapshots.get(bookId)?.tasks ?? []).filter(task => task.chapterId === chapter.id && task.state !== 'completed')
            updateChapterProgress(bookId, chapter.id, { state: remaining.some(task => task.state === 'failed') ? 'failed' : 'stopped', error: '本窗口已保存，其他窗口尚未完成。' })
            return
          }
          const message = reason instanceof Error ? reason.message : '章节完成状态更新失败'
          failedChapterIds.add(chapter.id)
          updateChapterProgress(bookId, chapter.id, { state: 'failed', error: message })
          return
        } finally {
          completingChapters.delete(chapter.id)
        }
        const message = `${prefix}：已完成；${usageLabel()}`
        reconcileCompletedChapter(bookId, chapter.id)
        setProgress(message)
        const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
        publishBatch(bookId, {
          message,
          chapterStates: {
            ...current.chapterStates,
            [chapter.id]: {
              ...(current.chapterStates[chapter.id] ?? {
                completedWindows: 0,
                totalWindows: 0,
                error: null,
              }),
              state: 'processed',
              error: null,
            },
          },
          catalogRevision: current.catalogRevision + 1,
        })
      })
    }

    try {
      if (selectedPlans.length === 0) {
        const filtered = requested.filter(chapter => chapterFilterReason(chapter.title, chapterFilter)).length
        const message = filtered ? `自动跳过处理 ${filtered} 章，其余章节已处理；本次无需调用模型。`
          : `所选 ${requested.length} 章均已处理，无需重复调用模型。`
        setProgress(message)
        publishBatch(bookId, { running: false, stopRequested: false, message })
        onFinished()
        return
      }

      nextRosterIndex = 1
      let rosterPromise: Promise<void> | null = trackWork(scheduleRoster(selectedPlans[0], 0))
      let index = 0
      while (true) {
        if (batchShouldStop(bookId, stopRef.current)) throw abortReason ?? new BatchAbortError('批量处理已停止')
        if (index >= selectedPlans.length) {
          if (inFlight.size === 0) {
            // Close admission synchronously with the empty-pool check, before yielding.
            expandableBatches.delete(bookId)
            break
          }
          await new Promise<void>(resolve => { wakeQueue = resolve })
          wakeQueue = null
          continue
        }
        const plan = selectedPlans[index]
        if (!rosterPromise) {
          nextRosterIndex = index + 1
          rosterPromise = trackWork(scheduleRoster(plan, index))
        }
        await rosterPromise
        rosterPromise = null
        if (batchShouldStop(bookId, stopRef.current)) throw new BatchAbortError('批量处理已停止')
        // 本章人物确认成功（或失败/空名单已记录）后，才启动下一章人物。
        // 下一章人物可与已确认章节的对白窗口并发，但人物任务之间绝不并发。
        if (index + 1 < selectedPlans.length) {
          nextRosterIndex = index + 2
          rosterPromise = trackWork(scheduleRoster(selectedPlans[index + 1], index + 1))
        }
        if (!plan.cancelled && !failedChapterIds.has(plan.chapter.id) && !skippedEmptyChapterIds.has(plan.chapter.id)) {
          trackWork(scheduleDialogue(plan, index))
        }
        index += 1
      }
      const outcomes = await Promise.allSettled(pendingWork)
      if (batchShouldStop(bookId, stopRef.current)) throw new BatchAbortError('批量处理已停止')
      const abortOutcome = outcomes.find((outcome) =>
        outcome.status === 'rejected' && isBatchAbortError(outcome.reason)
      )
      if (abortOutcome?.status === 'rejected') throw abortOutcome.reason
      const selectedCount = new Set([...selectedPlans, ...directRetryPlans].map(plan => plan.chapter.id)).size
      const filteredCount = requested.filter(chapter => chapterFilterReason(chapter.title, chapterFilter)).length
      const skipped = requested.length - selectedCount - filteredCount
      const succeededCount = selectedCount - failedChapterIds.size - skippedEmptyChapterIds.size - cancelledChapterIds.size
      const emptySummary = (filteredCount ? `，按名单自动跳过 ${filteredCount} 章` : '')
        + (skippedEmptyChapterIds.size ? `，跳过无人物章节 ${skippedEmptyChapterIds.size} 章` : '')
        + (cancelledChapterIds.size ? `，已取消 ${cancelledChapterIds.size} 章` : '')
      const summary = failedChapterIds.size > 0
        ? `批量处理结束：成功 ${succeededCount} 章，失败 ${failedChapterIds.size} 章${emptySummary}${skipped ? `，跳过已处理 ${skipped} 章` : ''}，${usageLabel()}。`
        : `批量处理完成：${forceReprocess ? '重做' : '新处理'} ${succeededCount} 章${emptySummary}${skipped ? `，跳过已处理 ${skipped} 章` : ''}，${usageLabel()}。`
      setProgress(summary)
      if (failedChapterIds.size > 0) {
        setError(`批量处理结束：${succeededCount} 章成功，${failedChapterIds.size} 章失败；具体原因见任务列表。`)
      }
      publishBatch(bookId, { running: false, stopRequested: false, message: retryTaskId &&
        batchSnapshots.get(bookId)?.chapterStates[requested[0]?.id]?.state !== 'processed'
        && selectedPlans.every(plan => (plan.estimate.windows ?? []).every(window => (batchSnapshots.get(bookId)?.tasks ?? [])
          .some(task => task.id === `dialogue:${plan.chapter.id}:${String(window.window_id)}` && task.state === 'completed')))
        ? '所选失败窗口已恢复并保存；本章还有未完成窗口，请继续选择重试。' : summary })
      onFinished()
    } catch (reason) {
      stopRef.current = true
      await Promise.allSettled(pendingWork)
      const message = reason instanceof Error ? reason.message : '批量处理失败'
      setError(message)
      const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
      publishBatch(bookId, {
        running: false,
        stopRequested: false,
        message,
        tasks: current.tasks.map((task) => task.state === 'queued'
          ? { ...task, state: 'cancelled' as const, error: null }
          : task),
        chapterStates: Object.fromEntries(Object.entries(current.chapterStates).map(([chapterId, chapter]) => [
          chapterId,
          ['queued', 'roster', 'dialogue'].includes(chapter.state)
            ? { ...chapter, state: 'stopped' as const, error: message, pendingTasks: 0 }
            : { ...chapter, pendingTasks: 0 },
        ])),
      })
    } finally {
      expandableBatches.delete(bookId)
      publishBatch(bookId, { finishedAt: Date.now() })
      batchStopRequests.delete(bookId)
    }
}

export function BatchProcessor({ bookId, bookVersionId, chapters, profiles, onFinished, showConfiguration = true, initialChapterId }: BatchProcessorProps) {
  const batchProgress = useBatchProgress(bookId)
  const notifiedFinish = useRef<number | null>(batchProgress.finishedAt)
  useEffect(() => {
    if (batchProgress.finishedAt && !batchProgress.running && notifiedFinish.current !== batchProgress.finishedAt) {
      notifiedFinish.current = batchProgress.finishedAt
      if (batchProgress.tasks.some(task => task.state === 'failed')) setError(batchProgress.message)
      onFinished()
    }
  }, [batchProgress.finishedAt, batchProgress.running, onFinished])
  const [startId, setStartId] = useState('')
  const [endId, setEndId] = useState('')
  const [preferences, setPreferences] = useProcessingPreferences()
  const [filterSettings] = useGeneralSettings()
  const filterSignature = JSON.stringify(normalizeChapterFilter(filterSettings))
  const { profileId, maxRecheckRounds, maxFormatRetries, concurrency } = preferences
  const strategyReason = dialogueStrategyDisabledReason(preferences.dialogueStrategy, maxRecheckRounds)
  const tokenLimitText = preferences.tokenLimit === null ? '' : String(preferences.tokenLimit)
  const setMaxRecheckRounds = (value: number) => setPreferences({ maxRecheckRounds: value })
  const setTokenLimitText = (value: string) => setPreferences({ tokenLimit: positiveIntegerOrNull(value) })
  const setConcurrency = (value: number) => setPreferences({ concurrency: value })
  const [forceReprocess, setForceReprocess] = useState(false)
  const [running, setRunning] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [taskListOpen, setTaskListOpen] = useState(true)
  const [estimatedTokens, setEstimatedTokens] = useState<number | null>(null)
  const [plans, setPlans] = useState<ChapterPlan[]>([])
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (profiles.length > 0 && !profiles.some(profile => profile.id === profileId)) {
      setPreferences({ profileId: profiles[0].id })
    }
  }, [profileId, profiles, setPreferences])

  const firstId = startId || chapters.find((chapter) => chapter.id === initialChapterId)?.id || chapters[0]?.id || ''
  const lastId = endId || chapters.at(-1)?.id || ''
  const startIndex = chapters.findIndex((chapter) => chapter.id === firstId)
  const endIndex = chapters.findIndex((chapter) => chapter.id === lastId)
  const validRange = startIndex >= 0 && endIndex >= startIndex

  const resetEstimate = () => {
    setEstimatedTokens(null)
    setPlans([])
  }

  // Shared settings can change from the single-chapter form or another tab.
  // Do not run a saved estimate using a different model/recheck policy.
  useEffect(() => {
    if (!running) {
      setEstimatedTokens(null)
      setPlans([])
    }
  }, [profileId, maxRecheckRounds, maxFormatRetries, tokenLimitText, running, preferences.dialogueStrategy, preferences.rosterRepairEnabled, preferences.maxRosterRepairs, filterSignature])

  const calculateEstimate = async () => {
    if (!validRange) return
    setEstimating(true)
    setError(null)
    setProgress('')
    try {
      const requested = chapters.slice(startIndex, endIndex + 1)
      const selected = requested.filter((chapter) => !chapterFilterReason(chapter.title, filterSettings)
        && (forceReprocess || !chapter.dialogue_processed))
      const estimates = await mapWithConcurrency(
        selected, 4, (chapter) => estimateRange(bookId, {
          bookVersionId,
          range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp, dialogueStrategy: preferences.dialogueStrategy },
          readingMode: 'reread',
          visibleHorizonCp: null,
          budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds, maxFormatRetries },
        }),
      )
      const currentPreferences = getProcessingPreferences()
      if (JSON.stringify(normalizeChapterFilter(getGeneralSettings())) !== filterSignature
        || currentPreferences.dialogueStrategy !== preferences.dialogueStrategy
        || currentPreferences.rosterRepairEnabled !== preferences.rosterRepairEnabled
        || currentPreferences.maxRosterRepairs !== preferences.maxRosterRepairs
        || currentPreferences.maxFormatRetries !== maxFormatRetries
        || currentPreferences.maxRecheckRounds !== maxRecheckRounds
        || currentPreferences.profileId !== profileId) {
        setProgress('处理配置已改变，请使用当前配置重新估算。')
        return
      }
      setPlans(selected.map((chapter, index) => ({ chapter, estimate: estimates[index] })))
      const rosterReserve = selected.reduce(
        (total, chapter) => total + estimateRosterTokens(chapter.end_cp - chapter.start_cp, preferences),
        0,
      )
      setEstimatedTokens(estimates.reduce((total, estimate) => total + estimate.total_tokens, 0) + rosterReserve)
      const filtered = requested.filter(chapter => chapterFilterReason(chapter.title, filterSettings)).length
      if (selected.length < requested.length) setProgress(`自动跳过处理 ${filtered} 章，跳过已处理 ${requested.length - selected.length - filtered} 章。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '批量 Token 估算失败')
    } finally {
      setEstimating(false)
    }
  }

  const run = async () => {
    if (!validRange || !profileId || !bookVersionId) return
    setRunning(true)
    try {
      const work: BatchExecution = { bookId, bookVersionId,
        requested: chapters.slice(startIndex, endIndex + 1), plans, preferences, forceReprocess,
        chapterFilter: normalizeChapterFilter(filterSettings),
      }
      await enqueueWork({ bookId, versionId: bookVersionId,
        title: `批量处理：${chapters[startIndex]?.title} → ${chapters[endIndex]?.title}`,
        keys: plans.flatMap(plan => [`roster:${plan.chapter.id}`, ...(plan.estimate.windows ?? [])
          .map(window => `dialogue:${plan.chapter.id}:${String(window.window_id)}`)]),
        payload: { type: 'batch', work } })
      setProgress('已添加到任务队列，可继续选择其他范围。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '添加任务失败')
    } finally { setRunning(false) }
  }
  const taskList = (() => {
    if (!batchProgress.running && batchProgress.tasks.length === 0) return null
    const finishedTasks = batchProgress.tasks.filter((task) =>
      ['completed', 'failed', 'cancelled'].includes(task.state),
    ).length
    const runningTasks = batchProgress.tasks.filter((task) => task.state === 'running').length
    return (
      <section
        id="batch-task-list"
        className="card ndr-step-card ndr-batch-progress"
        data-testid={batchProgress.running ? 'batch-progress-panel' : 'batch-result-panel'}
      >
        <div className="ndr-step-heading">
          <div>
            <h3>{batchProgress.running ? '批量处理进度' : '最近一次批量任务列表'}</h3>
            <OperationTimer startedAt={batchProgress.startedAt} finishedAt={batchProgress.finishedAt}
              completed={batchProgress.tasks.filter(task => task.state === 'completed').length} total={batchProgress.tasks.length} />
            <p className="hint" aria-live="polite" data-testid="batch-progress-message">
              {batchProgress.message}
            </p>
          </div>
          {batchProgress.running && <button
            type="button"
            className="ndr-danger"
            disabled={batchProgress.stopRequested}
            title={batchProgress.stopRequested ? '停止请求已提交，请等待已发出的模型请求安全收尾。' : undefined}
            onClick={() => requestBatchStop(bookId)}
            data-testid="batch-stop"
          >
            {batchProgress.stopRequested ? '正在停止…' : '停止批量处理'}
          </button>}
        </div>
        <div className="ndr-batch-progress-summary">
          <progress value={finishedTasks} max={Math.max(1, batchProgress.tasks.length)} />
          <span data-testid="batch-task-summary">
            已结束 {finishedTasks}/{batchProgress.tasks.length} 项；当前并发 {runningTasks} 项
          </span>
        </div>
        <CollapsibleBlock title="批量任务明细" open={taskListOpen} onOpenChange={setTaskListOpen}
          summary={<span>共 {batchProgress.tasks.length} 项；失败 {batchProgress.tasks.filter(task => task.state === 'failed').length} 项</span>}>
        <div className="ndr-table-wrap">
          <table className="ndr-batch-task-table">
            <thead>
              <tr>
                <th>状态</th>
                <th>处理类型</th>
                <th>章节</th>
                <th>窗口</th>
                <th>原因 / 错误详情</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {batchProgress.tasks.map((task) => (
                <tr key={task.id} data-task-state={task.state} data-testid="batch-task-row">
                  <td><span className={`ndr-task-state ndr-task-${task.state}`}>{TASK_STATE_LABELS[task.state]}</span></td>
                  <td>{task.type === 'roster' ? '人物识别' : '对白归属'}</td>
                  <td>{task.chapterTitle}</td>
                  <td title={task.windowId ?? undefined}>
                    {task.windowLabel}
                  </td>
                  <td className={task.error ? 'status-error' : undefined}>
                    {task.error || (task.type === 'roster'
                      ? batchProgress.chapterStates[task.chapterId]?.error
                      : null) || '—'}
                  </td>
                  <td><BatchRetryControls bookId={bookId} taskId={task.id} />
                    {task.jobId && <Link className="ndr-button" to={`/tasks?jobId=${encodeURIComponent(task.jobId)}`}>查看任务</Link>}
                    {task.state === 'failed' && !task.retryable && <span className="hint">请先查看任务详情，确认请求已结束后再处理。</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </CollapsibleBlock>
        <p className="hint">{batchProgress.running
          ? '停止后不会再派发排队任务；已经发给模型的请求会安全收尾。'
          : '最近一次任务明细会保存；跨书籍任务与已添加范围可从顶部“任务队列”查看。'}</p>
      </section>
    )
  })()

  if (!showConfiguration) return taskList

  return (
    <>
    {taskList}
    <section className="card ndr-step-card" data-testid="batch-processor">
      <div className="ndr-step-heading">
        <span className="ndr-step-badge" aria-hidden="true">1</span>
        <div>
          <h3>批量处理配置</h3>
          <p className="hint">人物按章节顺序确认；上一章人物完成后，本章对白窗口会与下一章人物识别共享并发任务池。</p>
        </div>
      </div>
      <div className="ndr-range-grid ndr-batch-config-grid">
        <label>
          开始章节
          <select value={firstId} onChange={(event) => { setStartId(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-start">
            {chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title || `第 ${chapter.ordinal + 1} 章`}</option>)}
          </select>
        </label>
        <label>
          结束章节
          <select value={lastId} onChange={(event) => { setEndId(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-end">
            {chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title || `第 ${chapter.ordinal + 1} 章`}</option>)}
          </select>
        </label>
        <label>
          每个窗口最多复核次数
          <input type="number" min={0} value={maxRecheckRounds} onChange={(event) => { setMaxRecheckRounds(nonNegativeInteger(event.target.value)); resetEstimate() }} disabled={running} data-testid="batch-max-rechecks" />
          <span className="hint">0 关闭；每轮检查原窗口全部对白（含已自动接受项），人工锁定结果不覆盖。每轮可能拆为多个模型调用并消耗 Tokens；校验失败重试另行设置。</span>
        </label>
        <FormatRetrySetting value={maxFormatRetries} disabled={running}
          onChange={value => { setPreferences({ maxFormatRetries: value }); resetEstimate() }} testId="batch-format-retries" />
        <label>
          本次 Token 使用上限
          <input type="number" min={1} value={tokenLimitText} onChange={(event) => { setTokenLimitText(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-token-limit" />
          <span className="hint">留空表示不限制。</span>
        </label>
        <label>
          最大并发任务数
          <input type="number" min={1} max={16} value={concurrency} onChange={(event) => setConcurrency(Math.min(16, Math.max(1, Number(event.target.value) || 1)))} disabled={running} data-testid="batch-concurrency" />
        </label>
      </div>
      <label className="ndr-radio-row">
        <input
          type="checkbox"
          checked={forceReprocess}
          disabled={running || estimating}
          onChange={(event) => { setForceReprocess(event.target.checked); resetEstimate() }}
          data-testid="batch-force-reprocess"
        />
        强制重做已处理章节（重新调用模型，可能产生费用）
      </label>
      {forceReprocess && (
        <p className="hint">所选范围内的全部章节将重新识别人物和处理对白，保留人工确认或锁定的标注。</p>
      )}
      <p className="hint">并发数同时约束人物识别和对白窗口；设为 1 即按顺序处理，建议从 2 开始。</p>
      {filterSettings.chapterFilterEnabled && <p className="hint">已启用章节过滤名单，匹配的章节会自动跳过处理，强制重做也不例外。<a href="/settings/general">调整过滤名单</a></p>}
      <div className="ndr-form-actions">
        <button type="button" className="ndr-primary" title={running ? '批量处理正在运行，请等待结束或先停止任务。' : estimating ? '正在估算 Token，请等待估算完成。' : !bookVersionId ? '书籍版本尚未读取，请先重新读取书籍。' : !validRange ? '请选择有效的开始和结束章节，结束章节不能早于开始章节。' : !profileId ? '请先选择模型配置，再估算和启动批量处理。' : strategyReason ?? undefined} disabled={running || estimating || !validRange || !profileId || !bookVersionId || Boolean(strategyReason)} onClick={() => void (estimatedTokens === null ? calculateEstimate() : run())} data-testid="batch-run">
          {running ? '批量处理中…' : estimating ? '正在估算…' : estimatedTokens === null ? '预估 Token' : '确认并开始批量处理'}
        </button>
      </div>
      {(running || estimating || !bookVersionId) && <p className="hint" role="status">{running ? '批量处理正在运行，请等待结束或先停止任务。' : estimating ? '正在估算 Token，请等待估算完成后再启动。' : '书籍版本尚未读取，请重新读取书籍后再启动。'}</p>}
      {estimatedTokens !== null && (
        <p className="hint" data-testid="batch-estimate">
          整批预计约 {estimatedTokens.toLocaleString()} tokens（包含逐章人物识别预留与对白归属估算）。
          {forceReprocess ? ' 已包含已处理章节的重做费用估算。' : chapters.slice(startIndex, endIndex + 1).some((chapter) => chapter.dialogue_processed) ? ' 已处理章节不会重复计费或处理。' : ''}
          {positiveIntegerOrNull(tokenLimitText) !== null && estimatedTokens > (positiveIntegerOrNull(tokenLimitText) ?? 0) ? ' 预计会超过当前上限，系统只会在剩余额度允许时派发新任务。' : ''}
        </p>
      )}
      {strategyReason && <p className="hint">{strategyReason}</p>}
      {!profileId && <p className="hint">请选择批量处理使用的模型配置。</p>}
      {!validRange && <p className="status-error">结束章节不能早于开始章节。</p>}
      {(progress || batchProgress.message) && <p className="hint" data-testid="batch-progress">{batchProgress.message || progress}</p>}
      {error && <p className="status-error" data-testid="batch-error">
        {error} {taskList && <a href="#batch-task-list" onClick={() => setTaskListOpen(true)}>查看任务列表</a>}
      </p>}
    </section>
    </>
  )
}
