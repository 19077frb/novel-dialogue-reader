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
const TASK_STATE_LABELS: Record<BatchTaskState, string> = {
  queued: '排队中',
  running: '处理中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已停止',
}

export type ChapterProcessingState = 'unprocessed' | 'processing' | 'processed'
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
  chapterStates: Record<string, ChapterProcessingState>
  tasks: BatchTaskProgress[]
  revision: number
}

const EMPTY_BATCH: BatchProgressSnapshot = {
  running: false,
  stopRequested: false,
  message: '',
  chapterStates: {},
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
}

export function BatchProcessor({ bookId, bookVersionId, chapters, profiles, onFinished }: BatchProcessorProps) {
  const batchProgress = useBatchProgress(bookId)
  const [startId, setStartId] = useState('')
  const [endId, setEndId] = useState('')
  const [profileId, setProfileId] = useState('')
  const [maxRechecks, setMaxRechecks] = useState(0)
  const [tokenLimitText, setTokenLimitText] = useState('')
  const [concurrency, setConcurrency] = useState(2)
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
      const selected = requested.filter((chapter) => !chapter.dialogue_processed)
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
    const selectedPlans = plans.filter(({ chapter }) => !chapter.dialogue_processed)
    let tokenLimit = positiveIntegerOrNull(tokenLimitText)
    let spent = 0
    let reserved = 0
    let warnedAtEightyPercent = false
    let warningOpen = false
    const limiter = createTaskLimiter(concurrency)
    const pendingDialogue: Promise<void>[] = []
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
      chapterStates: Object.fromEntries(requested.map((chapter) => [
        chapter.id,
        chapter.dialogue_processed ? 'processed' : 'unprocessed',
      ])),
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
      if (answer === null) throw new Error('已在额度接近上限时停止')
      const nextLimit = Number(answer.trim())
      if (nextLimit === 0) {
        spent = 0
        warnedAtEightyPercent = false
        publishBatch(bookId, { message: '额度已刷新，继续后台处理…' })
        return
      }
      if (!Number.isFinite(nextLimit) || nextLimit <= spent) {
        throw new Error(`新额度必须大于当前已使用的 ${spent.toLocaleString()} tokens`)
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
        if (available !== null && available <= 0) throw new Error('已达到本次 Token 使用上限')
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
        throw new Error(`模型没有返回${stage}用量，无法可靠执行 Token 上限，批量处理已停止`)
      }
      spent += jobTokens(job)
      checkBudgetReminder()
    }

    const scheduleRoster = (plan: ChapterPlan, index: number) => {
      const { chapter } = plan
      const taskId = `roster:${chapter.id}`
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
      publishBatch(bookId, {
        message: `${prefix}：正在识别人物…`,
        chapterStates: { ...(batchSnapshots.get(bookId)?.chapterStates ?? {}), [chapter.id]: 'processing' },
      })
      setProgress(`${prefix}：正在识别人物…`)
      return runMetered(Math.max(1, chapter.end_cp - chapter.start_cp) + 2_000, async (available) => {
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
          if (accepted.length === 0) throw new Error(`${prefix}没有识别到可确认的人物`)
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
          publishBatch(bookId, { message: `${prefix}：人物已确认，正在并发处理对白窗口…` })
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : '人物识别失败'
          updateBatchTask(bookId, taskId, batchShouldStop(bookId, stopRef.current) ? 'cancelled' : 'failed', message)
          throw reason
        }
      })
    }

    const scheduleDialogue = (plan: ChapterPlan, index: number) => {
      const { chapter, estimate } = plan
      const prefix = `${index + 1}/${selectedPlans.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
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
          } catch (reason) {
            const message = reason instanceof Error ? reason.message : '对白窗口处理失败'
            updateBatchTask(bookId, taskId, batchShouldStop(bookId, stopRef.current) ? 'cancelled' : 'failed', message)
            throw reason
          }
        })
      })
      return Promise.all(jobs).then(async () => {
        await completeChapterProcessing(bookId, chapter.id, bookVersionId)
        const message = `${prefix}：已完成；本次累计 ${spent.toLocaleString()} tokens`
        setProgress(message)
        publishBatch(bookId, {
          message,
          chapterStates: { ...(batchSnapshots.get(bookId)?.chapterStates ?? {}), [chapter.id]: 'processed' },
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

      let rosterPromise: Promise<void> | null = scheduleRoster(selectedPlans[0], 0)
      for (let index = 0; index < selectedPlans.length; index += 1) {
        await rosterPromise
        if (batchShouldStop(bookId, stopRef.current)) throw new Error('批量处理已停止')
        // 下一章人物任务先进入共享队列，使它能与本章的多个对白窗口并行。
        rosterPromise = index + 1 < selectedPlans.length
          ? scheduleRoster(selectedPlans[index + 1], index + 1)
          : null
        pendingDialogue.push(scheduleDialogue(selectedPlans[index], index))
      }
      await Promise.all(pendingDialogue)
      const skipped = requested.length - selectedPlans.length
      const summary = `批量处理完成：新处理 ${selectedPlans.length} 章${skipped ? `，跳过已处理 ${skipped} 章` : ''}，本次累计 ${spent.toLocaleString()} tokens。`
      setProgress(summary)
      publishBatch(bookId, { running: false, stopRequested: false, message: summary })
      onFinished()
    } catch (reason) {
      stopRef.current = true
      await Promise.allSettled(pendingDialogue)
      const message = reason instanceof Error ? reason.message : '批量处理失败'
      setError(message)
      publishBatch(bookId, { running: false, stopRequested: false, message })
    } finally {
      setRunning(false)
      batchStopRequests.delete(bookId)
    }
  }

  if (batchProgress.running) {
    const finishedTasks = batchProgress.tasks.filter((task) =>
      ['completed', 'failed', 'cancelled'].includes(task.state),
    ).length
    const runningTasks = batchProgress.tasks.filter((task) => task.state === 'running').length
    return (
      <section className="card ndr-step-card ndr-batch-progress" data-testid="batch-progress-panel">
        <div className="ndr-step-heading">
          <div>
            <h3>批量处理进度</h3>
            <p className="hint" aria-live="polite" data-testid="batch-progress-message">
              {batchProgress.message}
            </p>
          </div>
          <button
            type="button"
            className="ndr-danger"
            disabled={batchProgress.stopRequested}
            onClick={() => requestBatchStop(bookId)}
            data-testid="batch-stop"
          >
            {batchProgress.stopRequested ? '正在停止…' : '停止批量处理'}
          </button>
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
                    {task.error && <small className="status-error">{task.error}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="hint">停止后不会再派发排队任务；已经发给模型的请求会安全收尾。</p>
      </section>
    )
  }

  return (
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
      <p className="hint">并发数同时约束人物识别和对白窗口；设为 1 即按顺序处理，建议从 2 开始。</p>
      <div className="ndr-form-actions">
        <button type="button" className="ndr-primary" disabled={running || estimating || !validRange || !profileId || !bookVersionId} onClick={() => void (estimatedTokens === null ? calculateEstimate() : run())} data-testid="batch-run">
          {running ? '批量处理中…' : estimating ? '正在估算…' : estimatedTokens === null ? '预估 Token' : '确认并开始批量处理'}
        </button>
      </div>
      {estimatedTokens !== null && (
        <p className="hint" data-testid="batch-estimate">
          整批预计约 {estimatedTokens.toLocaleString()} tokens（包含逐章人物识别预留与对白归属估算）。
          {chapters.slice(startIndex, endIndex + 1).some((chapter) => chapter.dialogue_processed) ? ' 已处理章节不会重复计费或处理。' : ''}
          {positiveIntegerOrNull(tokenLimitText) !== null && estimatedTokens > (positiveIntegerOrNull(tokenLimitText) ?? 0) ? ' 预计会超过当前上限，系统只会在剩余额度允许时派发新任务。' : ''}
        </p>
      )}
      {!profileId && <p className="hint">请选择批量处理使用的模型配置。</p>}
      {!validRange && <p className="status-error">结束章节不能早于开始章节。</p>}
      {progress && <p className="hint" data-testid="batch-progress">{progress}</p>}
      {error && <p className="status-error" data-testid="batch-error">{error}</p>}
    </section>
  )
}
