import { useRef, useState } from 'react'

import { fetchJob } from '../api/books'
import {
  analyzeCharacterRoster,
  confirmCharacterRoster,
  fetchCharacterRoster,
} from '../api/characters'
import { createJob, freshIdempotencyKey } from '../api/jobs'
import type { ChapterOut, JobDetailOut } from '../api/types'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

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
  const [progress, setProgress] = useState('')
  const [error, setError] = useState<string | null>(null)
  const stopRef = useRef(false)

  const firstId = startId || chapters[0]?.id || ''
  const lastId = endId || chapters.at(-1)?.id || ''
  const startIndex = chapters.findIndex((chapter) => chapter.id === firstId)
  const endIndex = chapters.findIndex((chapter) => chapter.id === lastId)
  const validRange = startIndex >= 0 && endIndex >= startIndex

  const run = async () => {
    if (!validRange || !profileId) return
    const selected = chapters.slice(startIndex, endIndex + 1)
    const tokenLimit = positiveIntegerOrNull(tokenLimitText)
    let spent = 0
    stopRef.current = false
    setRunning(true)
    setError(null)

    try {
      for (let index = 0; index < selected.length; index += 1) {
        if (stopRef.current) throw new Error('批量处理已停止')
        const chapter = selected[index]
        const prefix = `${index + 1}/${selected.length} ${chapter.title || `第 ${chapter.ordinal + 1} 章`}`
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

        const roster = await fetchCharacterRoster(bookId, chapter.id, bookVersionId)
        const accepted = (roster.candidates ?? []).filter((candidate) => Boolean(candidate.canonical_name))
        if (accepted.length === 0) throw new Error(`${prefix}没有识别到可确认的人物`)
        const pov = accepted.find((candidate) => candidate.pov_candidate) ?? accepted[0]
        setProgress(`${prefix}：正在确认人物并判断对白…`)
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
        setProgress(`${prefix}：已完成；本次累计 ${spent.toLocaleString()} tokens`)
      }
      setProgress(`批量处理完成：共 ${selected.length} 章，本次累计 ${spent.toLocaleString()} tokens。`)
      onFinished()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '批量处理失败')
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
          <select value={firstId} onChange={(event) => setStartId(event.target.value)} disabled={running} data-testid="batch-start">
            {chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title || `第 ${chapter.ordinal + 1} 章`}</option>)}
          </select>
        </label>
        <label>
          结束章节
          <select value={lastId} onChange={(event) => setEndId(event.target.value)} disabled={running} data-testid="batch-end">
            {chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title || `第 ${chapter.ordinal + 1} 章`}</option>)}
          </select>
        </label>
        <label>
          本次 Token 使用上限（留空＝不限制）
          <input type="number" min={1} value={tokenLimitText} onChange={(event) => setTokenLimitText(event.target.value)} disabled={running} data-testid="batch-token-limit" />
        </label>
      </div>
      <div className="ndr-form-actions">
        <button type="button" className="ndr-primary" disabled={running || !validRange || !profileId} onClick={() => void run()} data-testid="batch-run">
          {running ? '批量处理中…' : '开始批量处理'}
        </button>
        {running && <button type="button" onClick={() => { stopRef.current = true; setProgress('正在安全停止…') }}>停止</button>}
      </div>
      {!profileId && <p className="hint">请先选择模型配置。</p>}
      {!validRange && <p className="status-error">结束章节不能早于开始章节。</p>}
      {progress && <p className="hint" data-testid="batch-progress">{progress}</p>}
      {error && <p className="status-error" data-testid="batch-error">{error}</p>}
    </section>
  )
}
