import { useRef, useState, useSyncExternalStore } from 'react'

import { fetchJob } from '../api/books'
import {
  analyzeCharacterRoster,
  confirmCharacterRoster,
  fetchCharacterRoster,
} from '../api/characters'
import { createJob, estimateRange, freshIdempotencyKey } from '../api/jobs'
import type { ChapterOut, JobDetailOut } from '../api/types'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

export type ChapterProcessingState = 'unprocessed' | 'processing' | 'processed'

export interface BatchProgressSnapshot {
  running: boolean
  message: string
  chapterStates: Record<string, ChapterProcessingState>
  revision: number
}

const EMPTY_BATCH: BatchProgressSnapshot = {
  running: false,
  message: '',
  chapterStates: {},
  revision: 0,
}
const batchSnapshots = new Map<string, BatchProgressSnapshot>()
const batchListeners = new Set<() => void>()

function publishBatch(bookId: string, update: Partial<BatchProgressSnapshot>) {
  const current = batchSnapshots.get(bookId) ?? EMPTY_BATCH
  batchSnapshots.set(bookId, { ...current, ...update, revision: current.revision + 1 })
  batchListeners.forEach((listener) => listener())
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

interface BatchProcessorProps {
  bookId: string
  bookVersionId: string | null | undefined
  chapters: ChapterOut[]
  profileId: string
  onFinished: () => void
}

export function BatchProcessor({
  bookId,
  bookVersionId,
  chapters,
  profileId,
  onFinished,
}: BatchProcessorProps) {
  const [startId, setStartId] = useState('')
  const [endId, setEndId] = useState('')
  const [tokenLimitText, setTokenLimitText] = useState('')
  const [running, setRunning] = useState(false)
  const [estimating, setEstimating] = useState(false)
  const [estimatedTokens, setEstimatedTokens] = useState<number | null>(null)
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const stopRef = useRef(false)

  const firstId = startId || chapters[0]?.id || ''
  const lastId = endId || chapters.at(-1)?.id || ''
  const startIndex = chapters.findIndex((chapter) => chapter.id === firstId)
  const endIndex = chapters.findIndex((chapter) => chapter.id === lastId)
  const validRange = startIndex >= 0 && endIndex >= startIndex

  const resetEstimate = () => setEstimatedTokens(null)

  const calculateEstimate = async () => {
    if (!validRange) return
    setEstimating(true)
    setError(null)
    try {
      const selected = chapters.slice(startIndex, endIndex + 1)
      const dialogueEstimates = await Promise.all(
        selected.map((chapter) =>
          estimateRange(bookId, {
            bookVersionId,
            range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp },
            readingMode: 'reread',
            visibleHorizonCp: null,
            budget: { maxInputTokens: null, maxOutputTokens: null, maxRechecks: 0 },
          }),
        ),
      )
      // 人物识别另有一次章节级调用：按章节文本长度估输入，并预留最多 2,000 输出 token。
      const rosterReserve = selected.reduce(
        (total, chapter) => total + Math.max(0, chapter.end_cp - chapter.start_cp) + 2_000,
        0,
      )
      setEstimatedTokens(
        dialogueEstimates.reduce((total, estimate) => total + estimate.total_tokens, 0) +
          rosterReserve,
      )
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '批量 Token 估算失败')
    } finally {
      setEstimating(false)
    }
  }

  const run = async () => {
    if (!validRange || !profileId) return
    const selected = chapters.slice(startIndex, endIndex + 1)
    let tokenLimit = positiveIntegerOrNull(tokenLimitText)
    let spent = 0
    let warnedAtEightyPercent = false
    stopRef.current = false
    setRunning(true)
    setError(null)
    publishBatch(bookId, {
      running: true,
      message: '准备批量处理…',
      chapterStates: Object.fromEntries(selected.map((chapter) => [chapter.id, 'unprocessed'])),
    })

    try {
      const checkBudgetReminder = () => {
        if (
          tokenLimit === null ||
          warnedAtEightyPercent ||
          spent < Math.ceil(tokenLimit * 0.8)
        ) return
        warnedAtEightyPercent = true
        const answer = window.prompt(
          `本次已使用约 ${spent.toLocaleString()} tokens，达到上限 ${tokenLimit.toLocaleString()} 的 80%。\n\n` +
          '请输入新的 Token 上限并直接继续；如果额度已经刷新，请输入 0，系统会按原上限从 0 重新计算。取消则安全停止。',
          String(tokenLimit),
        )
        if (answer === null) {
          throw new Error('已在额度接近上限时停止')
        }
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
        publishBatch(bookId, {
          message: `Token 上限已调整为 ${tokenLimit.toLocaleString()}，继续后台处理…`,
        })
      }

      for (let index = 0; index < selected.length; index += 1) {
        if (stopRef.current) throw new Error('批量处理已停止')
        const chapter = selected[index]
        const prefix = `${index + 1}/${selected.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
        const currentStates = batchSnapshots.get(bookId)?.chapterStates ?? {}
        publishBatch(bookId, {
          message: `${prefix}：正在识别人物…`,
          chapterStates: { ...currentStates, [chapter.id]: 'processing' },
        })
        let remaining = tokenLimit === null ? null : tokenLimit - spent
        if (remaining !== null && remaining <= 0) throw new Error('已达到本次 Token 使用上限')

        setProgress(`${prefix}：正在识别人物…`)
        const rosterJob = await waitForJob(
          await analyzeCharacterRoster(bookId, chapter.id, {
            bookVersionId,
            profileId,
            maxInputTokens: remaining,
            idempotencyKey: freshIdempotencyKey('batch-roster', `${bookId}:${chapter.id}:${profileId}`),
          }),
          () => stopRef.current,
        )
        if (rosterJob.state !== 'COMPLETED') {
          throw new Error(rosterJob.last_error || `${prefix}的人物识别未完成`)
        }
        spent += jobTokens(rosterJob)
        if (tokenLimit !== null && rosterJob.unknown_usage_runs > 0) {
          throw new Error('模型没有返回人物识别用量，无法可靠执行 Token 上限，批量处理已停止')
        }
        checkBudgetReminder()

        const roster = await fetchCharacterRoster(bookId, chapter.id, bookVersionId)
        const accepted = (roster.candidates ?? []).filter((candidate) => Boolean(candidate.canonical_name))
        if (accepted.length === 0) throw new Error(`${prefix}没有识别到可确认的人物`)
        const pov = accepted.find((candidate) => candidate.pov_candidate) ?? accepted[0]
        setProgress(`${prefix}：正在确认人物并判断对白…`)
        publishBatch(bookId, { message: `${prefix}：正在确认人物并判断对白…` })
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

        remaining = tokenLimit === null ? null : tokenLimit - spent
        if (remaining !== null && remaining <= 0) throw new Error('已达到本次 Token 使用上限')
        const dialogueJob = await waitForJob(
          await createJob({
            bookId,
            mode: 'process',
            bookVersionId,
            range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp },
            profileId,
            readingMode: 'reread',
            visibleHorizonCp: null,
            budget: { maxInputTokens: remaining, maxOutputTokens: remaining, maxRechecks: 0 },
            idempotencyKey: freshIdempotencyKey('batch-dialogue', `${bookId}:${chapter.id}:${profileId}`),
          }),
          () => stopRef.current,
        )
        spent += jobTokens(dialogueJob)
        if (dialogueJob.state !== 'COMPLETED') {
          throw new Error(dialogueJob.last_error || `${prefix}的对白归属未完成`)
        }
        if (tokenLimit !== null && dialogueJob.unknown_usage_runs > 0) {
          throw new Error('模型没有返回对白处理用量，无法可靠执行 Token 上限，批量处理已停止')
        }
        checkBudgetReminder()
        setProgress(`${prefix}：已完成；本次累计 ${spent.toLocaleString()} tokens`)
        publishBatch(bookId, {
          message: `${prefix}：已完成；本次累计 ${spent.toLocaleString()} tokens`,
          chapterStates: {
            ...(batchSnapshots.get(bookId)?.chapterStates ?? {}),
            [chapter.id]: 'processed',
          },
        })
      }
      setProgress(`批量处理完成：共 ${selected.length} 章，本次累计 ${spent.toLocaleString()} tokens。`)
      publishBatch(bookId, {
        running: false,
        message: `批量处理完成：共 ${selected.length} 章，本次累计 ${spent.toLocaleString()} tokens。`,
      })
      onFinished()
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '批量处理失败'
      setError(message)
      publishBatch(bookId, { running: false, message })
    } finally {
      setRunning(false)
    }
  }

  return (
    <section className="card ndr-step-card" data-testid="batch-processor">
      <div className="ndr-step-heading">
        <div>
          <h3>批量处理章节</h3>
          <p className="hint">按顺序逐章识别并自动确认人物，再判断对白；后续章节会沿用前文人物。</p>
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
          本次 Token 使用上限（留空＝不限制）
          <input type="number" min={1} value={tokenLimitText} onChange={(event) => { setTokenLimitText(event.target.value); resetEstimate() }} disabled={running} data-testid="batch-token-limit" />
        </label>
      </div>
      <div className="ndr-form-actions">
        <button
          type="button"
          className="ndr-primary"
          disabled={running || estimating || !validRange || !profileId}
          onClick={() => void (estimatedTokens === null ? calculateEstimate() : run())}
          data-testid="batch-run"
        >
          {running ? '批量处理中…' : estimating ? '正在估算…' : estimatedTokens === null ? '预估 Token' : '确认并开始批量处理'}
        </button>
        {running && <button type="button" onClick={() => { stopRef.current = true; setProgress('正在安全停止…') }}>停止</button>}
      </div>
      {estimatedTokens !== null && (
        <p className="hint" data-testid="batch-estimate">
          整批预计约 {estimatedTokens.toLocaleString()} tokens（包含逐章人物识别预留与对白归属估算）。
          {positiveIntegerOrNull(tokenLimitText) !== null && estimatedTokens > (positiveIntegerOrNull(tokenLimitText) ?? 0)
            ? ' 预计会超过当前上限，任务将在额度不足时停止。'
            : ''}
        </p>
      )}
      {!profileId && <p className="hint">请先选择模型配置。</p>}
      {!validRange && <p className="status-error">结束章节不能早于开始章节。</p>}
      {progress && <p className="hint" data-testid="batch-progress">{progress}</p>}
      {error && <p className="status-error" data-testid="batch-error">{error}</p>}
    </section>
  )
}
