import { useEffect, useRef, useState, useSyncExternalStore } from 'react'

import { completeChapterProcessing, fetchJob } from '../api/books'
import {
  analyzeCharacterRoster,
  confirmCharacterRoster,
  fetchCharacterRoster,
} from '../api/characters'
import { createJob, estimateRange, freshIdempotencyKey } from '../api/jobs'
import type { ChapterOut, EstimateOut, JobDetailOut, ModelProfileOut } from '../api/types'
import { createTaskLimiter } from '../processing/concurrency'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

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
export interface ChapterProcessingProgress {
  state: ChapterProcessingState
  completedWindows: number
  totalWindows: number
  error: string | null
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
}

export interface BatchProgressSnapshot {
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

function publishBatch(bookId: string, update: Partial<BatchProgressSnapshot>) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  batchSnapshots.set(bookId, { ...current, ...update, revision: current.revision + 1 })
  batchListeners.forEach((listener) => listener())
}

function updateBatchTask(
  bookId: string,
  taskId: string,
  state: BatchTaskState,
  error: string | null = null,
) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  publishBatch(bookId, {
    tasks: current.tasks.map((task) => task.id === taskId ? { ...task, state, error } : task),
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

export function requestBatchStop(bookId: string) {
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
      progress.state === 'queued' ? { ...progress, state: 'stopped' as const } : progress,
    ])),
  })
}

export function useBatchProgress(bookId: string | undefined): BatchProgressSnapshot {
  return useSyncExternalStore(
    (listener) => {
      batchListeners.add(listener)
      return () => batchListeners.delete(listener)
    },
    () => (bookId ? batchSnapshots.get(bookId) ?? EMPTY_BATCH : EMPTY_BATCH),
  )
}

function subscribeBatch(listener: () => void) {
  batchListeners.add(listener)
  return () => batchListeners.delete(listener)
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
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

async function waitForJob(job: JobDetailOut, stopped: () => boolean): Promise<JobDetailOut> {
  let current = job
  while (!TERMINAL_STATES.has(current.state)) {
    if (stopped()) throw new Error('批量处理已停止')
    await new Promise((resolve) => window.setTimeout(resolve, 800))
    current = await fetchJob(current.id)
  }
  if (stopped()) throw new Error('批量处理已停止')
  return current
}

interface ChapterPlan {
  chapter: ChapterOut
  estimate: EstimateOut
}

interface BatchProcessorProps {
  bookId: string
  bookVersionId: string | null | undefined
  chapters: ChapterOut[]
  profiles: ModelProfileOut[]
  onFinished: () => void
  showConfiguration?: boolean
}

export function BatchProcessor({ bookId, bookVersionId, chapters, profiles, onFinished, showConfiguration = true }: BatchProcessorProps) {
  const batchProgress = useBatchProgress(bookId)
  const [startId, setStartId] = useState('')
  const [endId, setEndId] = useState('')
  const [profileId, setProfileId] = useState('')
  const [maxRechecks, setMaxRechecks] = useState(0)
  const [tokenLimitText, setTokenLimitText] = useState('')
  const [concurrency, setConcurrency] = useState(2)
  const [forceReprocess, setForceReprocess] = useState(false)
  const [running, setRunning] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [estimatedTokens, setEstimatedTokens] = useState<number | null>(null)
  const [plans, setPlans] = useState<ChapterPlan[]>([])
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const stopRef = useRef(false)

  useEffect(() => {
    if (!profileId && profiles.length > 0) setProfileId(profiles[0].id)
  }, [profileId, profiles])

  const firstId = startId || chapters[0]?.id || ''
  const lastId = endId || chapters.at(-1)?.id || ''
  const startIndex = chapters.findIndex((chapter) => chapter.id === firstId)
  const endIndex = chapters.findIndex((chapter) => chapter.id === lastId)
  const validRange = startIndex >= 0 && endIndex >= startIndex

  const resetEstimate = () => {
    setEstimatedTokens(null)
    setPlans([])
  }

  const calculateEstimate = async () => {
    if (!validRange) return
    setEstimating(true)
    setError(null)
    setProgress('')
    try {
      const requested = chapters.slice(startIndex, endIndex + 1)
      const selected = requested.filter((chapter) => forceReprocess || !chapter.dialogue_processed)
      const estimates = await Promise.all(
        selected.map((chapter) => estimateRange(bookId, {
          bookVersionId,
          range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp },
          readingMode: 'reread',
          visibleHorizonCp: null,
          budget: { maxInputTokens: null, maxOutputTokens: null, maxRechecks },
        })),
      )
      setPlans(selected.map((chapter, index) => ({ chapter, estimate: estimates[index] })))
      const rosterReserve = selected.reduce(
        (total, chapter) => total + Math.max(0, chapter.end_cp - chapter.start_cp) + 2_000,
        0,
      )
      setEstimatedTokens(estimates.reduce((total, estimate) => total + estimate.total_tokens, 0) + rosterReserve)
      if (selected.length < requested.length) setProgress(`已跳过 ${requested.length - selected.length} 个已处理章节。`)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '批量 Token 估算失败')
    } finally {
      setEstimating(false)
    }
  }

  const run = async () => {
    if (!validRange || !profileId || !bookVersionId) return
    const requested = chapters.slice(startIndex, endIndex + 1)
    const selectedPlans = plans.filter(({ chapter }) => forceReprocess || !chapter.dialogue_processed)
    let tokenLimit = positiveIntegerOrNull(tokenLimitText)
    let spent = 0
    let reserved = 0
    let warnedAtEightyPercent = false
    let warningOpen = false
    const limiter = createTaskLimiter(concurrency)
    const pendingWork: Promise<void>[] = []
    const failedChapterIds = new Set<string>()
    const skippedEmptyChapterIds = new Set<string>()
    stopRef.current = false
    batchStopRequests.delete(bookId)
    setRunning(true)
    setError(null)
    const tasks: BatchTaskProgress[] = selectedPlans.flatMap(({ chapter, estimate }) => {
      const chapterTitle = chapter.title || `第 ${chapter.ordinal + 1} 章`
      return [
        {
          id: `roster:${chapter.id}`,
          type: 'roster' as const,
          chapterId: chapter.id,
          chapterTitle,
          windowId: null,
          windowLabel: '整章人物识别',
          state: 'queued' as const,
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
    })
    publishBatch(bookId, {
      running: true,
      stopRequested: false,
      message: '准备批量处理…',
      tasks,
      chapterStates: Object.fromEntries(requested.map((chapter) => {
        const plan = selectedPlans.find((item) => item.chapter.id === chapter.id)
        return [chapter.id, {
          state: plan ? 'queued' : chapter.dialogue_processed ? 'processed' : 'unprocessed',
          completedWindows: 0,
          totalWindows: plan?.estimate.windows?.length ?? 0,
          error: null,
        } satisfies ChapterProcessingProgress]
      })),
    })

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
        warnedAtEightyPercent = false
        publishBatch(bookId, { message: '额度已刷新，继续后台处理…' })
        return
      }
      if (!Number.isFinite(nextLimit) || nextLimit <= spent) {
        throw new BatchAbortError(`新额度必须大于当前已使用的 ${spent.toLocaleString()} tokens`)
      }
      tokenLimit = Math.floor(nextLimit)
      setTokenLimitText(String(tokenLimit))
      warnedAtEightyPercent = spent >= Math.ceil(tokenLimit * 0.8)
      publishBatch(bookId, { message: `Token 上限已调整为 ${tokenLimit.toLocaleString()}，继续后台处理…` })
    }

    const runMetered = <T,>(reserveEstimate: number, task: (available: number | null) => Promise<T>) =>
      limiter.run(async () => {
        if (batchShouldStop(bookId, stopRef.current)) throw new Error('批量处理已停止')
        const available = tokenLimit === null ? null : tokenLimit - spent - reserved
        if (available !== null && available <= 0) {
          throw new BatchAbortError('已达到本次 Token 使用上限')
        }
        // 用启动前的本地估算预留额度；所有在途任务的预留之和不会超过
        // 当前批次剩余额度，完成后再按提供方返回的实测用量结算。
        const reservation = available === null
          ? 0
          : Math.min(Math.max(1, reserveEstimate), available)
        reserved += reservation
        try {
          return await task(available === null ? null : reservation)
        } finally {
          reserved = Math.max(0, reserved - reservation)
        }
      })

    const recordUsage = (job: JobDetailOut, stage: string) => {
      if (tokenLimit !== null && job.unknown_usage_runs > 0) {
        throw new BatchAbortError(`模型没有返回${stage}用量，无法可靠执行 Token 上限，批量处理已停止`)
      }
      spent += jobTokens(job)
      checkBudgetReminder()
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
      const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
      publishBatch(bookId, {
        tasks: current.tasks.map((task) => task.chapterId === chapterId && task.state === 'queued'
          ? { ...task, state: 'cancelled' as const, error: null }
          : task),
      })
    }

    const scheduleRoster = (plan: ChapterPlan, index: number) => {
      const { chapter } = plan
      const taskId = `roster:${chapter.id}`
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
      return runMetered(Math.max(1, chapter.end_cp - chapter.start_cp) + 2_000, async (available) => {
        updateChapterProgress(bookId, chapter.id, { state: 'roster', error: null })
        publishBatch(bookId, { message: `${prefix}：正在识别人物…` })
        setProgress(`${prefix}：正在识别人物…`)
        updateBatchTask(bookId, taskId, 'running')
        try {
          const rosterJob = await waitForJob(
            await analyzeCharacterRoster(bookId, chapter.id, {
              bookVersionId,
              profileId,
              maxInputTokens: available,
              idempotencyKey: freshIdempotencyKey('batch-roster', `${bookId}:${chapter.id}:${profileId}`),
            }),
            () => batchShouldStop(bookId, stopRef.current),
          )
          if (rosterJob.state !== 'COMPLETED') throw new Error(rosterJob.last_error || `${prefix}的人物识别未完成`)
          recordUsage(rosterJob, '人物识别')
          const roster = await fetchCharacterRoster(bookId, chapter.id, bookVersionId)
          const accepted = (roster.candidates ?? []).filter((candidate) => Boolean(candidate.canonical_name))
          if (accepted.length === 0) {
            skippedEmptyChapterIds.add(chapter.id)
            updateBatchTask(bookId, taskId, 'completed')
            updateChapterProgress(bookId, chapter.id, {
              state: 'stopped', error: '未识别到人物，已跳过本章对白处理',
            })
            const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
            publishBatch(bookId, {
              message: `${prefix}：未识别到人物，跳过本章并继续下一章`,
              tasks: current.tasks.map((task) => task.chapterId === chapter.id && task.state === 'queued'
                ? { ...task, state: 'cancelled' as const, error: null }
                : task),
            })
            return
          }
          const pov = accepted.find((candidate) => candidate.pov_candidate) ?? accepted[0]
          await confirmCharacterRoster(bookId, chapter.id, {
            bookVersionId,
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
          updateBatchTask(bookId, taskId, 'completed')
          updateChapterProgress(bookId, chapter.id, { state: 'dialogue', error: null })
          publishBatch(bookId, { message: `${prefix}：人物已确认，正在并发处理对白窗口…` })
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : '人物识别失败'
          const stopped = batchShouldStop(bookId, stopRef.current)
          markChapterTaskFailure(chapter.id, taskId, stopped ? 'cancelled' : 'failed', message)
          if (stopped) throw new BatchAbortError('批量处理已停止')
          if (isBatchAbortError(reason)) throw reason
        }
      })
    }

    const scheduleDialogue = (plan: ChapterPlan, index: number) => {
      const { chapter, estimate } = plan
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
      let chapterFailed = false
      const jobs = (estimate.windows ?? []).map((window) => {
        const windowId = String(window.window_id)
        const taskId = `dialogue:${chapter.id}:${windowId}`
        return runMetered(Number(window.estimated_tokens) || 1, async (available) => {
          updateBatchTask(bookId, taskId, 'running')
          try {
            const dialogueJob = await waitForJob(
              await createJob({
                bookId,
                mode: 'process',
                bookVersionId,
                range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp },
                selectedWindowIds: [windowId],
                forceReprocess,
                profileId,
                readingMode: 'reread',
                visibleHorizonCp: null,
                budget: { maxInputTokens: available, maxOutputTokens: available, maxRechecks },
                idempotencyKey: freshIdempotencyKey(
                  'batch-dialogue-window',
                  `${bookId}:${chapter.id}:${windowId}:${profileId}:${maxRechecks}`,
                ),
              }),
              () => batchShouldStop(bookId, stopRef.current),
            )
            if (dialogueJob.state !== 'COMPLETED') throw new Error(dialogueJob.last_error || `${prefix}的窗口 ${windowId} 未完成`)
            recordUsage(dialogueJob, '对白处理')
            updateBatchTask(bookId, taskId, 'completed')
            recordCompletedWindow(bookId, chapter.id)
          } catch (reason) {
            const message = reason instanceof Error ? reason.message : '对白窗口处理失败'
            const stopped = batchShouldStop(bookId, stopRef.current)
            markChapterTaskFailure(chapter.id, taskId, stopped ? 'cancelled' : 'failed', message)
            chapterFailed = true
            if (stopped) throw new BatchAbortError('批量处理已停止')
            if (isBatchAbortError(reason)) throw reason
          }
        })
      })
      return Promise.all(jobs).then(async () => {
        if (chapterFailed || failedChapterIds.has(chapter.id)) return
        try {
          await completeChapterProcessing(bookId, chapter.id, bookVersionId)
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : '章节完成状态更新失败'
          failedChapterIds.add(chapter.id)
          updateChapterProgress(bookId, chapter.id, { state: 'failed', error: message })
          return
        }
        const message = `${prefix}：已完成；本次累计 ${spent.toLocaleString()} tokens`
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
        const message = `所选 ${requested.length} 章均已处理，无需重复调用模型。`
        setProgress(message)
        publishBatch(bookId, { running: false, stopRequested: false, message })
        onFinished()
        return
      }

      const trackWork = (promise: Promise<void>) => {
        pendingWork.push(promise)
        // 后台对白与下一章人物同时运行，提前注册拒绝处理，最终统一收集结果。
        void promise.catch(() => undefined)
        return promise
      }
      let rosterPromise = trackWork(scheduleRoster(selectedPlans[0], 0))
      for (let index = 0; index < selectedPlans.length; index += 1) {
        const plan = selectedPlans[index]
        await rosterPromise
        if (batchShouldStop(bookId, stopRef.current)) throw new BatchAbortError('批量处理已停止')
        // 本章人物确认成功（或失败/空名单已记录）后，才启动下一章人物。
        // 下一章人物可与已确认章节的对白窗口并发，但人物任务之间绝不并发。
        if (index + 1 < selectedPlans.length) {
          rosterPromise = trackWork(scheduleRoster(selectedPlans[index + 1], index + 1))
        }
        if (!failedChapterIds.has(plan.chapter.id) && !skippedEmptyChapterIds.has(plan.chapter.id)) {
          trackWork(scheduleDialogue(plan, index))
        }
      }
      const outcomes = await Promise.allSettled(pendingWork)
      const abortOutcome = outcomes.find((outcome) =>
        outcome.status === 'rejected' && isBatchAbortError(outcome.reason)
      )
      if (abortOutcome?.status === 'rejected') throw abortOutcome.reason
      const skipped = requested.length - selectedPlans.length
      const succeededCount = selectedPlans.length - failedChapterIds.size - skippedEmptyChapterIds.size
      const emptySummary = skippedEmptyChapterIds.size ? `，跳过无人物章节 ${skippedEmptyChapterIds.size} 章` : ''
      const summary = failedChapterIds.size > 0
        ? `批量处理结束：成功 ${succeededCount} 章，失败 ${failedChapterIds.size} 章${emptySummary}${skipped ? `，跳过已处理 ${skipped} 章` : ''}，本次累计 ${spent.toLocaleString()} tokens。`
        : `批量处理完成：${forceReprocess ? '重做' : '新处理'} ${succeededCount} 章${emptySummary}${skipped ? `，跳过已处理 ${skipped} 章` : ''}，本次累计 ${spent.toLocaleString()} tokens。`
      setProgress(summary)
      if (failedChapterIds.size > 0) {
        setError(`批量处理结束：${succeededCount} 章成功，${failedChapterIds.size} 章失败；具体原因见任务列表。`)
      }
      publishBatch(bookId, { running: false, stopRequested: false, message: summary })
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
            ? { ...chapter, state: 'stopped' as const, error: message }
            : chapter,
        ])),
      })
    } finally {
      setRunning(false)
      batchStopRequests.delete(bookId)
    }
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
            <p className="hint" aria-live="polite" data-testid="batch-progress-message">
              {batchProgress.message}
            </p>
          </div>
          {batchProgress.running && <button
            type="button"
            className="ndr-danger"
            disabled={batchProgress.stopRequested}
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
        <div className="ndr-table-wrap">
          <table className="ndr-batch-task-table">
            <thead>
              <tr>
                <th>状态</th>
                <th>处理类型</th>
                <th>章节</th>
                <th>窗口</th>
                <th>原因 / 错误详情</th>
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">{batchProgress.running
          ? '停止后不会再派发排队任务；已经发给模型的请求会安全收尾。'
          : '任务列表保留到下一批启动，可在应用内切换页面后返回查看；刷新或关闭页面会清除本列表。'}</p>
      </section>
    )
  })()

  if (batchProgress.running || !showConfiguration) return taskList

  return (
    <>
    {taskList}
    <section className="card ndr-step-card" data-testid="batch-processor">
      <div className="ndr-step-heading">
        <div>
          <h3>批量处理配置</h3>
          <p className="hint">人物按章节顺序确认；上一章人物完成后，本章对白窗口会与下一章人物识别共享并发任务池。</p>
        </div>
      </div>
      <div className="ndr-range-grid">
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
          模型配置
          <select value={profileId} onChange={(event) => { setProfileId(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-profile">
            <option value="">（请选择模型配置）</option>
            {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name} · {profile.model}</option>)}
          </select>
        </label>
        <label>
          每个窗口最多复核数
          <input type="number" min={0} value={maxRechecks} onChange={(event) => { setMaxRechecks(nonNegativeInteger(event.target.value)); resetEstimate() }} disabled={running} data-testid="batch-max-rechecks" />
        </label>
        <label>
          本次 Token 使用上限（留空＝不限制）
          <input type="number" min={1} value={tokenLimitText} onChange={(event) => { setTokenLimitText(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-token-limit" />
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
      <div className="ndr-form-actions">
        <button type="button" className="ndr-primary" disabled={running || estimating || !validRange || !profileId || !bookVersionId} onClick={() => void (estimatedTokens === null ? calculateEstimate() : run())} data-testid="batch-run">
          {running ? '批量处理中…' : estimating ? '正在估算…' : estimatedTokens === null ? '预估 Token' : '确认并开始批量处理'}
        </button>
      </div>
      {estimatedTokens !== null && (
        <p className="hint" data-testid="batch-estimate">
          整批预计约 {estimatedTokens.toLocaleString()} tokens（包含逐章人物识别预留与对白归属估算）。
          {forceReprocess ? ' 已包含已处理章节的重做费用估算。' : chapters.slice(startIndex, endIndex + 1).some((chapter) => chapter.dialogue_processed) ? ' 已处理章节不会重复计费或处理。' : ''}
          {positiveIntegerOrNull(tokenLimitText) !== null && estimatedTokens > (positiveIntegerOrNull(tokenLimitText) ?? 0) ? ' 预计会超过当前上限，系统只会在剩余额度允许时派发新任务。' : ''}
        </p>
      )}
      {!profileId && <p className="hint">请选择批量处理使用的模型配置。</p>}
      {!validRange && <p className="status-error">结束章节不能早于开始章节。</p>}
      {progress && <p className="hint" data-testid="batch-progress">{progress}</p>}
      {error && <p className="status-error" data-testid="batch-error">
        {error} {taskList && <a href="#batch-task-list">查看任务列表</a>}
      </p>}
    </section>
    </>
  )
}
