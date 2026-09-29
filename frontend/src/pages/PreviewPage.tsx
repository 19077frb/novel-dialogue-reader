import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { annotationKeys, fetchAnnotations } from '../api/annotations'
import {
  completeChapterProcessing,
  fetchBook,
  fetchChapters,
  fetchContent,
  fetchQuotes,
  queryKeys,
} from '../api/books'
import {
  createJob,
  estimateRange,
  freshIdempotencyKey,
  fetchUsage,
  jobKeys,
  type BudgetInput,
} from '../api/jobs'
import { fetchProfiles, profileKeys } from '../api/profiles'
import type { AnnotationItemOut, EstimateOut, JobDetailOut } from '../api/types'
import { BudgetForm } from '../components/BudgetForm'
import { BatchProcessor } from '../components/BatchProcessor'
import { CharacterRosterPanel } from '../components/CharacterRosterPanel'
import type { CandidateRange } from '../components/DocumentRenderer'
import { DocumentRenderer } from '../components/DocumentRenderer'
import { EstimateSummary } from '../components/EstimateSummary'
import { ExportDialog } from '../components/ExportDialog'
import { JobPanel } from '../components/JobPanel'
import { RangePicker, type RangeValue } from '../components/RangePicker'
import { SpeakerLegend } from '../components/SpeakerLegend'
import { UsageSummary } from '../components/UsageSummary'
import { mapWithConcurrency } from '../processing/concurrency'

const DEFAULT_BUDGET: BudgetInput = {
  maxInputTokens: 200_000,
  maxOutputTokens: null,
  maxRechecks: 0,
}

// 处理只生成一套完整标注；初读/重读仅由阅读页在展示投影时切换。
const PROCESSING_READING_MODE = 'reread' as const

/**
 * 预览页：范围选择 → 本地估算 → 小范围试运行 → 任务面板 → 原文/标注对比。
 *
 * - 预览与正式处理调用**同一套** `POST /api/jobs`（`mode: preview | process`），
 *   结果直接写进同一份标注投影，没有第二套临时识别存储。
 * - 原文/标注切换、图例点击都只改前端渲染，不发起任何模型调用。
 * - 任务面板显示后端的真实 `calls` / `cached_windows`，不伪造成功结果。
 */
export default function PreviewPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const queryClient = useQueryClient()
  const documentRef = useRef<HTMLDivElement>(null)
  const initializedRef = useRef(false)

  const [range, setRange] = useState<RangeValue>({ chapterId: null, startCp: 0, endCp: null })
  const [processingMode, setProcessingMode] = useState<'single' | 'batch'>('single')
  const [budget, setBudget] = useState<BudgetInput>(DEFAULT_BUDGET)
  const [concurrency, setConcurrency] = useState(2)
  const [profileId, setProfileId] = useState('')
  const [viewMode, setViewMode] = useState<'annotated' | 'original'>('annotated')
  const [estimate, setEstimate] = useState<EstimateOut | null>(null)
  const [selectedWindowIds, setSelectedWindowIds] = useState<string[]>([])
  const [jobId, setJobId] = useState<string | null>(null)
  const [currentJob, setCurrentJob] = useState<JobDetailOut | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [rosterConfirmed, setRosterConfirmed] = useState(false)

  const book = useQuery({
    queryKey: queryKeys.book(bookId ?? ''),
    queryFn: ({ signal }) => fetchBook(bookId as string, signal),
    enabled: Boolean(bookId),
  })
  const chapters = useQuery({
    queryKey: queryKeys.chapters(bookId ?? ''),
    queryFn: ({ signal }) => fetchChapters(bookId as string, signal),
    enabled: Boolean(bookId),
  })
  const profiles = useQuery({
    queryKey: profileKeys.profiles(),
    queryFn: ({ signal }) => fetchProfiles(signal),
  })

  // 默认范围：第一章（只做一次，之后完全由用户控制）。
  useEffect(() => {
    if (initializedRef.current) return
    const list = chapters.data
    if (!list || list.length === 0) return
    initializedRef.current = true
    setRange({ chapterId: list[0].id, startCp: list[0].start_cp, endCp: list[0].end_cp })
  }, [chapters.data])

  useEffect(() => {
    if (profileId !== '' || !profiles.data || profiles.data.length === 0) return
    setProfileId(profiles.data[0].id)
  }, [profiles.data, profileId])

  const canonicalLengthCp = book.data?.active_version?.canonical_length_cp ?? 0
  const resolvedEnd = range.endCp ?? canonicalLengthCp
  const rangeValid = canonicalLengthCp > 0 && resolvedEnd > range.startCp

  const content = useQuery({
    queryKey: queryKeys.content(bookId ?? '', range.chapterId, 'preview'),
    queryFn: ({ signal }) =>
      fetchContent(
        bookId as string,
        { chapterId: range.chapterId, limit: 500 },
        signal,
      ),
    enabled: Boolean(bookId) && rangeValid,
  })
  const quotes = useQuery({
    queryKey: queryKeys.quotes(bookId ?? '', range.chapterId),
    queryFn: ({ signal }) => fetchQuotes(bookId as string, { chapterId: range.chapterId, limit: 500 }, signal),
    enabled: Boolean(bookId) && rangeValid,
  })
  const annotations = useQuery({
    queryKey: annotationKeys.range(
      bookId ?? '',
      range.startCp,
      resolvedEnd,
      PROCESSING_READING_MODE,
      null,
    ),
    queryFn: ({ signal }) =>
      fetchAnnotations(
        bookId as string,
        {
          startCp: range.startCp,
          endCp: resolvedEnd,
          readingMode: PROCESSING_READING_MODE,
          visibleHorizonCp: null,
        },
        signal,
      ),
    enabled: Boolean(bookId) && rangeValid,
  })
  const usage = useQuery({
    queryKey: jobKeys.usage(bookId ?? ''),
    queryFn: ({ signal }) => fetchUsage(bookId as string, signal),
    enabled: Boolean(bookId),
  })

  const nodes = content.data?.nodes ?? []
  const candidates = useMemo<CandidateRange[]>(
    () =>
      (quotes.data?.items ?? []).map((quote) => ({
        quoteId: quote.quote_id,
        startCp: quote.start_cp,
        endCp: quote.end_cp,
        nestingDepth: quote.nesting_depth,
      })),
    [quotes.data],
  )
  const items = annotations.data?.items ?? []
  const visibleItems = useMemo<AnnotationItemOut[]>(
    () => (viewMode === 'annotated' ? items : []),
    [items, viewMode],
  )

  const estimateMutation = useMutation({
    mutationFn: () =>
      estimateRange(bookId as string, {
        bookVersionId: book.data?.active_version_id ?? null,
        range: { chapterId: range.chapterId, startCp: range.startCp, endCp: resolvedEnd },
        readingMode: PROCESSING_READING_MODE,
        visibleHorizonCp: null,
        budget,
      }),
    onSuccess: (data) => {
      setEstimate(data)
      setSelectedWindowIds(
        (data.windows ?? []).map((window) => String(window.window_id)),
      )
      setError(null)
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '估算失败'),
  })

  const jobMutation = useMutation({
    mutationFn: async (mode: 'preview' | 'process') => {
      const versionId = book.data?.active_version_id ?? null
      const plannedWindows = range.chapterId && estimate?.windows?.length
        ? (estimate.windows ?? []).filter((window) => selectedWindowIds.includes(String(window.window_id)))
        : []
      if (plannedWindows.length === 0) {
        return createJob({
          bookId: bookId as string,
          mode,
          bookVersionId: versionId,
          range: { chapterId: range.chapterId, startCp: range.startCp, endCp: resolvedEnd },
          selectedWindowIds: null,
          profileId: profileId || null,
          readingMode: PROCESSING_READING_MODE,
          visibleHorizonCp: null,
          budget,
          idempotencyKey: freshIdempotencyKey(`${mode}:${bookId}`, JSON.stringify({ versionId, range, profileId, budget })),
          runNow: true,
        })
      }

      const totalEstimated = plannedWindows.reduce(
        (total, window) => total + Math.max(1, Number(window.estimated_tokens) || 1),
        0,
      )
      const allocate = (limit: number | null, estimated: number) =>
        limit === null ? null : Math.max(1, Math.floor((limit * estimated) / totalEstimated))
      const jobs = await mapWithConcurrency(plannedWindows, concurrency, async (window) => {
        const windowId = String(window.window_id)
        const estimated = Math.max(1, Number(window.estimated_tokens) || 1)
        return createJob({
          bookId: bookId as string,
          mode,
          bookVersionId: versionId,
          range: { chapterId: range.chapterId, startCp: range.startCp, endCp: resolvedEnd },
          selectedWindowIds: [windowId],
          profileId: profileId || null,
          readingMode: PROCESSING_READING_MODE,
          visibleHorizonCp: null,
          budget: {
            maxInputTokens: allocate(budget.maxInputTokens, estimated),
            maxOutputTokens: allocate(budget.maxOutputTokens, estimated),
            maxRechecks: budget.maxRechecks,
          },
          idempotencyKey: freshIdempotencyKey(
            `${mode}:${bookId}:window`,
            JSON.stringify({ versionId, range, profileId, budget, windowId }),
          ),
          runNow: true,
        })
      })
      const failedJob = jobs.find((job) => job.state !== 'COMPLETED')
      if (failedJob) {
        throw new Error(failedJob.last_error || `窗口任务结束于 ${failedJob.state}`)
      }
      if (
        mode === 'process' &&
        range.chapterId &&
        versionId &&
        plannedWindows.length === (estimate?.windows?.length ?? 0)
      ) {
        await completeChapterProcessing(bookId as string, range.chapterId, versionId)
      }
      return jobs.at(-1) as JobDetailOut
    },
    onSuccess: (job) => {
      setJobId(job.id)
      setCurrentJob(job)
      setError(null)
      setNotice(null)
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '创建任务失败'),
  })

  const handleJobUpdate = useCallback(
    (job: JobDetailOut) => {
      setCurrentJob(job)
      if (!['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'].includes(job.state)) {
        return
      }
      setNotice(
        job.state === 'COMPLETED'
          ? '任务完成：结果已写入与正式阅读相同的标注投影（没有另一套临时存储）。'
          : `任务结束于 ${job.state}，请在任务面板查看原因。`,
      )
      // 失效**整族**投影查询：阅读页用的是「初读 horizon = 本章末端」的另一个键，
      // 只失效 horizon=null 那一个会让阅读页继续用旧（可能是空）的结果。
      void queryClient.invalidateQueries({ queryKey: ['annotations'] })
      void queryClient.invalidateQueries({ queryKey: jobKeys.usage(bookId ?? '') })
    },
    [bookId, queryClient],
  )

  const focusQuote = useCallback((quoteId: string | null | undefined) => {
    if (!quoteId) return
    const container = documentRef.current
    const target = container?.querySelector(`[data-quote-id="${quoteId}"]`)
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [])

  const handleRosterConfirmedChange = useCallback((confirmed: boolean) => {
    setRosterConfirmed(confirmed)
  }, [])

  // 本章人物是逐句归属的前置：只有选中单一章节时才要求先确认名单与主人公；
  // 整本或自定义码点范围没有“本章”，后端也不会注入章节人物名单。
  const rosterRequired = range.chapterId !== null
  const runBlockers: string[] = []
  if (!rangeValid) runBlockers.push('处理范围无效')
  if (profileId === '') runBlockers.push('未选择模型配置（见第一步）')
  if (rosterRequired && !rosterConfirmed) runBlockers.push('尚未确认本章人物（见第二步）')
  if (range.chapterId && estimate?.windows?.length && selectedWindowIds.length === 0) {
    runBlockers.push('尚未选择要处理的窗口')
  }
  const runDisabled = runBlockers.length > 0 || jobMutation.isPending

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  return (
    <div className="ndr-page ndr-preview">
      <header className="ndr-reader-header card ndr-page-header">
        <div>
          <h2>预览与按章处理：{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            先选择单章或批量处理，再展开对应配置；处理结果直接用于正式阅读。
          </p>
        </div>
        <nav className="ndr-preview-nav" aria-label="本书导航">
          <Link to={`/books/${bookId}/read`}>去阅读</Link>
          <Link to={`/books/${bookId}/review`}>待确认队列</Link>
          <button type="button" onClick={() => setExportOpen(true)} data-testid="open-export">
            导出
          </button>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      {book.isError && <p className="status-error">书籍读取失败。</p>}
      {chapters.isError && <p className="status-error">目录读取失败。</p>}

      <section className="card ndr-step-card" data-testid="processing-mode-picker">
        <div className="ndr-step-heading">
          <div>
            <h3>选择处理方式</h3>
            <p className="hint">单章可挑选窗口试运行；批量会自动识别人物并按章节流水线处理。</p>
          </div>
        </div>
        <div className="ndr-radio-row">
          <label>
            <input
              type="radio"
              name="processing-mode"
              checked={processingMode === 'single'}
              onChange={() => setProcessingMode('single')}
              data-testid="processing-mode-single"
            />
            单章处理
          </label>
          <label>
            <input
              type="radio"
              name="processing-mode"
              checked={processingMode === 'batch'}
              onChange={() => setProcessingMode('batch')}
              data-testid="processing-mode-batch"
            />
            批量处理
          </label>
        </div>
      </section>

      {processingMode === 'single' && (
        <>

      <section className="card ndr-preview-controls ndr-step-card">
        <div className="ndr-step-heading">
          <span className="ndr-step-badge" aria-hidden="true">1</span>
          <div>
            <h3>选择处理范围与模型</h3>
            <p className="hint">决定要处理的范围和使用哪个模型配置。</p>
          </div>
        </div>
        <RangePicker
          chapters={chapters.data ?? []}
          value={range}
          canonicalLengthCp={canonicalLengthCp}
          onChange={(next) => {
            setRange(next)
            setEstimate(null)
            setSelectedWindowIds([])
            setNotice(null)
          }}
        />
        <fieldset className="ndr-preview-model">
          <legend>模型配置</legend>
          <label>
            模型配置
            <select
              value={profileId}
              onChange={(event) => setProfileId(event.target.value)}
              data-testid="preview-profile"
            >
              <option value="">（请选择模型配置）</option>
              {(profiles.data ?? []).map((profile) => (
                <option key={profile.id} value={profile.id}>
                  {profile.name} · {profile.protocol} · {profile.model}
                </option>
              ))}
            </select>
          </label>
          {profiles.data && profiles.data.length === 0 && (
            <p className="hint" data-testid="preview-no-profile">
              还没有模型配置：请先到“模型配置”添加提供方（真实提供方需要密钥）。
            </p>
          )}
        </fieldset>
      </section>

      <CharacterRosterPanel
        step={2}
        bookId={bookId}
        bookVersionId={book.data?.active_version_id ?? null}
        chapterId={range.chapterId}
        profileId={profileId}
        onConfirmedChange={handleRosterConfirmedChange}
      />

      <section className="card ndr-preview-controls ndr-step-card">
        <div className="ndr-step-heading">
          <span className="ndr-step-badge" aria-hidden="true">3</span>
          <div>
            <h3>对白归属（试运行与正式处理）</h3>
            <p className="hint">
              人物确认后才能开始逐句归属；建议先小范围试运行确认效果，再按范围正式处理。
            </p>
          </div>
        </div>
        <BudgetForm value={budget} onChange={setBudget} />
        <fieldset className="ndr-budget-form">
          <legend>并发限制</legend>
          <label>
            最大并发窗口数
            <input
              type="number"
              min={1}
              max={16}
              value={concurrency}
              onChange={(event) => setConcurrency(Math.min(16, Math.max(1, Number(event.target.value) || 1)))}
              data-testid="preview-concurrency"
            />
          </label>
          <p className="hint">只影响所选对白窗口；人物识别仍会先读取本章全文。</p>
        </fieldset>
        <div className="ndr-form-actions">
          <button
            type="button"
            onClick={() => estimateMutation.mutate()}
            disabled={!rangeValid || estimateMutation.isPending}
            data-testid="preview-estimate"
          >
            本地估算（不调用模型）
          </button>
          <button
            type="button"
            className="ndr-primary"
            onClick={() => jobMutation.mutate('preview')}
            disabled={runDisabled}
            data-testid="preview-run"
          >
            试运行预览（调用模型）
          </button>
          <button
            type="button"
            onClick={() => jobMutation.mutate('process')}
            disabled={runDisabled}
            data-testid="preview-process"
          >
            按此范围正式处理
          </button>
        </div>
        {runBlockers.length > 0 && (
          <p className="hint" data-testid="preview-run-hint">
            暂不能开始对白归属：{runBlockers.join('；')}。
          </p>
        )}
        {error && (
          <p className="status-error" data-testid="preview-error">
            {error}
          </p>
        )}
        {notice && (
          <p className="hint" data-testid="preview-notice">
            {notice}
          </p>
        )}
        {estimate && <EstimateSummary estimate={estimate} />}
        {range.chapterId && (estimate?.windows?.length ?? 0) > 0 && (
          <fieldset className="ndr-window-picker" data-testid="window-picker">
            <legend>选择要处理的窗口（可多选）</legend>
            <p className="hint">人物识别仍会读取本章全文；这里只限制对白归属窗口。</p>
            {(estimate?.windows ?? []).map((window) => {
              const windowId = String(window.window_id)
              const checked = selectedWindowIds.includes(windowId)
              return (
                <label key={windowId} className="ndr-window-option">
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={(event) =>
                      setSelectedWindowIds((current) =>
                        event.target.checked
                          ? [...current, windowId]
                          : current.filter((item) => item !== windowId),
                      )
                    }
                    data-testid={`window-${windowId}`}
                  />
                  <span>
                    窗口 {String(window.ordinal)} · {String(window.target_count)} 句对白 · 约{' '}
                    {Number(window.estimated_tokens).toLocaleString()} tokens
                    <small>{String(window.preview || '（无文本预览）')}</small>
                  </span>
                </label>
              )
            })}
          </fieldset>
        )}
      </section>

      {jobId && (
        <section className="card">
          <h2>任务</h2>
          <JobPanel jobId={jobId} onUpdate={handleJobUpdate} />
          <div className="ndr-recompute-entry" data-testid="recompute-entry">
            <p className="hint">
              任务**不会**自动重算：暂停/限流/预算到顶或失败后，都需要你显式重新发起。
              已完成窗口命中缓存，不会重复计费；只有未完成的窗口会真正调用模型。
            </p>
            <button
              type="button"
              disabled={runDisabled}
              onClick={() => jobMutation.mutate('process')}
              data-testid="preview-recompute"
            >
              用当前预算重新处理此范围（可能计费）
            </button>
          </div>
        </section>
      )}
        </>
      )}

      {processingMode === 'batch' && (
        <BatchProcessor
          bookId={bookId}
          bookVersionId={book.data?.active_version_id}
          chapters={chapters.data ?? []}
          profiles={profiles.data ?? []}
          onFinished={() => {
            void queryClient.invalidateQueries({ queryKey: ['annotations'] })
            void queryClient.invalidateQueries({ queryKey: queryKeys.chapters(bookId) })
            void queryClient.invalidateQueries({ queryKey: jobKeys.usage(bookId) })
          }}
        />
      )}

      <section className="card">
        <h2>用量</h2>
        {usage.isPending && <p className="hint">正在读取用量…</p>}
        {usage.isError && <p className="status-error">用量读取失败。</p>}
        {usage.data && <UsageSummary usage={usage.data} currentJob={currentJob} />}
      </section>

      <section className="card">
        <div className="ndr-preview-viewbar">
          <div className="ndr-radio-row">
            <label>
              <input
                type="radio"
                name="preview-view"
                checked={viewMode === 'original'}
                onChange={() => setViewMode('original')}
                data-testid="view-original"
              />
              原文（不显示颜色）
            </label>
            <label>
              <input
                type="radio"
                name="preview-view"
                checked={viewMode === 'annotated'}
                onChange={() => setViewMode('annotated')}
                data-testid="view-annotated"
              />
              标注（颜色与编号）
            </label>
          </div>
          <p className="hint" data-testid="view-summary">
            切换视图只改变显示，不会调用模型。当前范围 {range.startCp} – {resolvedEnd}，
            已标注 {items.length} 条，候选 {candidates.length} 条。
          </p>
        </div>

        {viewMode === 'annotated' && (
          <div className="ndr-preview-side">
            <div>
              <h3>说话人图例</h3>
              <SpeakerLegend
                legend={annotations.data?.legend ?? []}
                onFocus={(item) => focusQuote(item.first_quote_id)}
              />
            </div>
            {annotations.data && (
              <dl className="ndr-annotation-counts" data-testid="annotation-counts">
                <dt>总计</dt>
                <dd>{annotations.data.counts.total}</dd>
                <dt>已接受</dt>
                <dd>{annotations.data.counts.accepted}</dd>
                <dt>待确认</dt>
                <dd>{annotations.data.counts.provisional}</dd>
                <dt>未知</dt>
                <dd>{annotations.data.counts.unknown}</dd>
                <dt>后文证据（暂不显示）</dt>
                <dd>{annotations.data.counts.withheld}</dd>
                <dt>尚未处理</dt>
                <dd>{annotations.data.counts.unprocessed_quotes}</dd>
              </dl>
            )}
          </div>
        )}

                <div
          className="ndr-reader-content ndr-preview-document"
          data-testid="preview-document"
          ref={documentRef}
        >
          {nodes.length === 0 && <p className="hint">这个范围还没有可显示的正文。</p>}
          {nodes.length > 0 && (
            <DocumentRenderer
              bookId={bookId}
              nodes={nodes}
              candidates={viewMode === 'annotated' ? candidates : []}
              annotations={visibleItems}
            />
          )}
        </div>
      </section>
      <ExportDialog
        bookId={bookId}
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        chapters={chapters.data ?? []}
        readPositionCp={book.data?.read_position_cp ?? 0}
      />
    </div>
  )
}
