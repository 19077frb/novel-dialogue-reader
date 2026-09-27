import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { annotationKeys, fetchAnnotations } from '../api/annotations'
import { fetchBook, fetchChapters, fetchContent, fetchQuotes, queryKeys } from '../api/books'
import {
  createJob,
  estimateRange,
  fetchUsage,
  jobKeys,
  shortHash,
  type BudgetInput,
} from '../api/jobs'
import { fetchProfiles, profileKeys } from '../api/profiles'
import type { AnnotationItemOut, EstimateOut, JobDetailOut, ReadingMode } from '../api/types'
import { BudgetForm } from '../components/BudgetForm'
import type { CandidateRange } from '../components/DocumentRenderer'
import { DocumentRenderer } from '../components/DocumentRenderer'
import { EstimateSummary } from '../components/EstimateSummary'
import { ExportDialog } from '../components/ExportDialog'
import { JobPanel } from '../components/JobPanel'
import { RangePicker, type RangeValue } from '../components/RangePicker'
import { SpeakerLegend } from '../components/SpeakerLegend'
import { UsageSummary } from '../components/UsageSummary'

const DEFAULT_BUDGET: BudgetInput = {
  maxInputTokens: 200_000,
  maxOutputTokens: null,
  maxRechecks: 0,
}

/**
 * T11 预览页：范围选择 → 本地估算 → 小范围试运行 → 任务面板 → 原文/标注对比。
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
  const [budget, setBudget] = useState<BudgetInput>(DEFAULT_BUDGET)
  const [readingMode, setReadingMode] = useState<ReadingMode>('initial')
  const [profileId, setProfileId] = useState('')
  const [viewMode, setViewMode] = useState<'annotated' | 'original'>('annotated')
  const [estimate, setEstimate] = useState<EstimateOut | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)

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
      readingMode,
      null,
    ),
    queryFn: ({ signal }) =>
      fetchAnnotations(
        bookId as string,
        {
          startCp: range.startCp,
          endCp: resolvedEnd,
          readingMode,
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
        range: { startCp: range.startCp, endCp: resolvedEnd },
        readingMode,
        visibleHorizonCp: null,
        budget,
      }),
    onSuccess: (data) => {
      setEstimate(data)
      setError(null)
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '估算失败'),
  })

  const jobMutation = useMutation({
    mutationFn: (mode: 'preview' | 'process') =>
      createJob({
        bookId: bookId as string,
        mode,
        bookVersionId: book.data?.active_version_id ?? null,
        range: { startCp: range.startCp, endCp: resolvedEnd },
        profileId: profileId || null,
        readingMode,
        visibleHorizonCp: null,
        budget,
        // 同输入 → 同幂等键（用短摘要，避免超过后端 128 字符上限）：重复点击复用同一任务。
        // preview 与 process 的键不同，但缓存键相同：预览过的范围正式处理时不会重复调用模型。
        idempotencyKey: `${mode}:${bookId}:${shortHash(
          JSON.stringify({ start: range.startCp, end: resolvedEnd, readingMode, profileId, budget }),
        )}`,
        runNow: true,
      }),
    onSuccess: (job) => {
      setJobId(job.id)
      setError(null)
      setNotice(null)
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '创建任务失败'),
  })

  const handleTerminal = useCallback(
    (job: JobDetailOut) => {
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
    [bookId, queryClient, range.startCp, readingMode, resolvedEnd],
  )

  const focusQuote = useCallback((quoteId: string | null | undefined) => {
    if (!quoteId) return
    const container = documentRef.current
    const target = container?.querySelector(`[data-quote-id="${quoteId}"]`)
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [])

  const runDisabled = !rangeValid || profileId === '' || jobMutation.isPending

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  return (
    <div className="ndr-page ndr-preview">
      <header className="ndr-reader-header card">
        <div>
          <h2>预览与按章处理：{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            先用小范围试运行确认效果，再按章处理；预览结果直接复用到正式阅读。
          </p>
        </div>
        <nav className="ndr-preview-nav">
          <Link to={`/books/${bookId}/read`}>去阅读</Link>
          <button type="button" onClick={() => setExportOpen(true)} data-testid="open-export">
            导出
          </button>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      {book.isError && <p className="status-error">书籍读取失败。</p>}
      {chapters.isError && <p className="status-error">目录读取失败。</p>}

      <section className="card ndr-preview-controls">
        <RangePicker
          chapters={chapters.data ?? []}
          value={range}
          canonicalLengthCp={canonicalLengthCp}
          onChange={(next) => {
            setRange(next)
            setEstimate(null)
            setNotice(null)
          }}
        />
        <BudgetForm value={budget} onChange={setBudget} />
        <fieldset className="ndr-preview-model">
          <legend>模型与阅读模式</legend>
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
          <label>
            阅读模式
            <select
              value={readingMode}
              onChange={(event) => setReadingMode(event.target.value as ReadingMode)}
              data-testid="preview-reading-mode"
            >
              <option value="initial">初读（不提前显示后文证据）</option>
              <option value="reread">重读（显示全部有效投影）</option>
            </select>
          </label>
          {profiles.data && profiles.data.length === 0 && (
            <p className="hint" data-testid="preview-no-profile">
              还没有模型配置：请先到“模型配置”添加提供方（真实提供方需要密钥）。
            </p>
          )}
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
      </section>

      {jobId && (
        <section className="card">
          <h2>任务</h2>
          <JobPanel jobId={jobId} onTerminal={handleTerminal} />
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

      <section className="card">
        <h2>用量</h2>
        {usage.isPending && <p className="hint">正在读取用量…</p>}
        {usage.isError && <p className="status-error">用量读取失败。</p>}
        {usage.data && <UsageSummary usage={usage.data} />}
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