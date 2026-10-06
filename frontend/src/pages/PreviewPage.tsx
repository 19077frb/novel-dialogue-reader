import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { CollapsibleBlock } from '../components/CollapsibleBlock'

import { annotationKeys, fetchAnnotations } from '../api/annotations'
import {
  completeChapterProcessing,
  setChapterProcessingStatus,
  fetchBook,
  fetchChapters,
  fetchContent,
  fetchQuotes,
  queryKeys,
} from '../api/books'
import {
  estimateRange,
  dialogueStrategyDisabledReason,
  freshIdempotencyKey,
  fetchUsage,
  fetchRecentJobs,
  jobKeys,
  type BudgetInput,
} from '../api/jobs'
import { fetchProfiles, profileKeys } from '../api/profiles'
import type { AnnotationItemOut, ChapterOut, JobDetailOut } from '../api/types'
import { BudgetForm } from '../components/BudgetForm'
import { OperationTimer, useRequestClock } from '../components/OperationTimer'
import { BatchProcessor, reconcileCompletedChapter, useBatchProgress } from '../components/BatchProcessor'
import { CharacterRosterPanel } from '../components/CharacterRosterPanel'
import type { CandidateRange } from '../components/DocumentRenderer'
import { DocumentRenderer } from '../components/DocumentRenderer'
import { EstimateSummary } from '../components/EstimateSummary'
import { ExportDialog } from '../components/ExportDialog'
import { JobPanel } from '../components/JobPanel'
import { RangePicker, type RangeValue } from '../components/RangePicker'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import { SpeakerLegend } from '../components/SpeakerLegend'
import { UsageSummary } from '../components/UsageSummary'
import { WindowPicker } from '../components/WindowPicker'
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'
import { runSingleWorkflow, useSingleWorkflow } from '../processing/singleWorkflow'
import type { SingleWorkflow } from '../processing/singleWorkflow'
import { inferenceOptions, useProcessingPreferences } from '../processing/preferences'
import { ThinkingSettings } from '../components/ThinkingSettings'
import { DialogueStrategySettings } from '../components/DialogueStrategySettings'
import { RosterRepairSettings } from '../components/RosterRepairSettings'

interface SingleWindowTask {
  windowId: string
  ordinal: string
  job: JobDetailOut | null
  error: string | null
}

const SINGLE_TASK_LABELS: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '处理中', PAUSING: '正在停止', PAUSED: '已停止',
  COMPLETED: '已完成', FAILED: '失败', BUDGET_EXHAUSTED: '额度耗尽',
  PARTIAL: '部分完成', NEEDS_RECONCILIATION: '需要确认调用结果',
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
  const [searchParams] = useSearchParams()
  const requestedChapterId = searchParams.get('chapterId')
  const queryClient = useQueryClient()
  const documentRef = useRef<HTMLDivElement>(null)
  const initializedRef = useRef<string | null>(null)

  const [range, setRange] = useState<RangeValue>({ chapterId: null, startCp: 0, endCp: null })
  const [processingMode, setProcessingMode] = useState<'single' | 'batch'>('single')
  const [preferences, setPreferences] = useProcessingPreferences()
  const { concurrency, profileId } = preferences
  const options = inferenceOptions(preferences)
  const budget = useMemo<BudgetInput>(() => ({
    maxInputTokens: preferences.tokenLimit, maxOutputTokens: preferences.maxOutputTokens,
    maxRecheckRounds: preferences.maxRecheckRounds,
    maxFormatRetries: preferences.maxFormatRetries,
  }), [preferences.tokenLimit, preferences.maxOutputTokens, preferences.maxRecheckRounds, preferences.maxFormatRetries])
  const setBudget = (value: BudgetInput) => setPreferences({
    tokenLimit: value.maxInputTokens, maxOutputTokens: value.maxOutputTokens,
    maxRecheckRounds: value.maxRecheckRounds,
    maxFormatRetries: value.maxFormatRetries ?? 1,
  })
  const setConcurrency = (value: number) => setPreferences({ concurrency: value })
  const setProfileId = useCallback((value: string) => setPreferences({ profileId: value }), [setPreferences])
  const [viewMode, setViewMode] = useState<'annotated' | 'original'>('annotated')
  const [selectedWindowIds, setSelectedWindowIds] = useState<string[]>([])
  const selectionKeyRef = useRef('')
  const selectionTouchedRef = useRef(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const manuallySelectedJobRef = useRef<string | null>(null)
  const taskDetailRef = useRef<HTMLElement | null>(null)
  const [detailRequest, setDetailRequest] = useState(0)
  const selectAutomaticJob = (id: string) => {
    if (!manuallySelectedJobRef.current) setJobId(id)
  }
  const showTaskDetails = (id: string) => {
    manuallySelectedJobRef.current = id
    setJobId(id)
    setDetailRequest(current => current + 1)
  }
  useEffect(() => {
    if (!detailRequest) return
    taskDetailRef.current?.scrollIntoView?.({ block: 'start', behavior: 'auto' })
    taskDetailRef.current?.focus({ preventScroll: true })
  }, [detailRequest, jobId])
  const [currentJob, setCurrentJob] = useState<JobDetailOut | null>(null)
  const [singleTasks, setSingleTasks] = useState<SingleWindowTask[]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [rosterConfirmed, setRosterConfirmed] = useState(false)
  const batchProgress = useBatchProgress(bookId)
  const singleProgress = useSingleWorkflow(bookId)
  const restoredWindows = useRef(new Set<string>())

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
  const recentJobs = useQuery({ queryKey: ['recent-single-jobs', bookId, book.data?.active_version_id, range.chapterId],
    queryFn: ({ signal }) => fetchRecentJobs({ bookId: bookId!, versionId: book.data!.active_version_id!,
      chapterId: range.chapterId ?? undefined, kind: 'INFERENCE' }, signal),
    enabled: Boolean(book.data?.active_version_id) && !singleProgress,
  })
  useEffect(() => {
    if (singleProgress || !recentJobs.data?.length) return
    const jobs = recentJobs.data
    const active = jobs.find(job => !TERMINAL_JOB_STATES.has(job.state)) ?? jobs[0]
    setCurrentJob(active)
    setJobId(active.id)
  }, [recentJobs.data, singleProgress])

  useEffect(() => {
    if (!singleProgress || singleProgress.versionId !== book.data?.active_version_id) return
    setSingleTasks(singleProgress.tasks)
    const job = singleProgress.tasks.find(task => task.job && !TERMINAL_JOB_STATES.has(task.job.state))?.job
      ?? singleProgress.tasks.filter(task => task.job).at(-1)?.job
    if (job) { setCurrentJob(job); if (!manuallySelectedJobRef.current) setJobId(job.id) }
    if (singleProgress.running) {
      setProcessingMode('single')
      setRange({ chapterId: singleProgress.chapterId, startCp: singleProgress.startCp, endCp: singleProgress.endCp })
    }
    if (singleProgress.error) setError(singleProgress.error)
    const completed = singleProgress.tasks.filter(task => task.job?.state === 'COMPLETED' && !restoredWindows.current.has(task.job.id))
    if (completed.length) {
      completed.forEach(task => restoredWindows.current.add(task.job!.id))
      void queryClient.invalidateQueries({ queryKey: ['window-preview', bookId] })
      void queryClient.invalidateQueries({ queryKey: ['annotations'] })
      void queryClient.invalidateQueries({ queryKey: jobKeys.usage(bookId ?? '') })
    }
    if (!singleProgress.running && !singleProgress.error && singleProgress.completeChapter && singleProgress.chapterId) {
      reconcileCompletedChapter(bookId!, singleProgress.chapterId)
      void queryClient.invalidateQueries({ queryKey: queryKeys.chapters(bookId!) })
    }
  }, [singleProgress, book.data?.active_version_id, bookId, queryClient])

  // 阅读页传入当前章节；按本书目录验证，只在进入该链接时初始化，之后由用户控制。
  useEffect(() => {
    const key = JSON.stringify([bookId, requestedChapterId])
    if (initializedRef.current === key) return
    const list = chapters.data
    if (!list || list.length === 0) return
    const chapter = list.find((item) => item.id === requestedChapterId) ?? list[0]
    initializedRef.current = key
    setRange({ chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp })
  }, [bookId, requestedChapterId, chapters.data])

  useEffect(() => {
    if (!profiles.data || profiles.data.some(profile => profile.id === profileId)) return
    setProfileId(profiles.data[0]?.id ?? '')
  }, [profiles.data, profileId, setProfileId])

  const canonicalLengthCp = book.data?.active_version?.canonical_length_cp ?? 0
  const returnChapterId = chapters.data?.some((chapter) => chapter.id === requestedChapterId)
    ? requestedChapterId : range.chapterId
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

  const estimateQuery = useQuery({
    queryKey: ['window-preview', bookId, book.data?.active_version_id, range, resolvedEnd, budget, preferences.dialogueStrategy],
    queryFn: ({ signal }) =>
      estimateRange(bookId as string, {
        bookVersionId: book.data?.active_version_id ?? null,
        range: { chapterId: range.chapterId, startCp: range.startCp, endCp: resolvedEnd, dialogueStrategy: preferences.dialogueStrategy },
        readingMode: PROCESSING_READING_MODE,
        visibleHorizonCp: null,
        budget,
      }, signal),
    enabled: Boolean(bookId) && rangeValid && range.chapterId !== null
      && processingMode === 'single' && !batchProgress.running
      && !dialogueStrategyDisabledReason(preferences.dialogueStrategy, budget.maxRecheckRounds),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    retry: false,
  })
  const estimate = rangeValid ? estimateQuery.data ?? null : null
  const estimateClock = useRequestClock(estimateQuery.isFetching)
  useEffect(() => {
    if (!estimate) return
    const ids = (estimate.windows ?? []).map((window) => String(window.window_id))
    const key = JSON.stringify([bookId, estimate.book_version_id, range.chapterId, range.startCp, resolvedEnd, ids])
    if (key === selectionKeyRef.current && selectionTouchedRef.current) return
    if (key !== selectionKeyRef.current) selectionTouchedRef.current = false
    selectionKeyRef.current = key
    setSelectedWindowIds((estimate.windows ?? []).filter(window => window.processing_status !== 'completed').map(window => String(window.window_id)))
  }, [estimate, bookId, range.chapterId, range.startCp, resolvedEnd])

  const jobMutation = useMutation({
    mutationFn: async (mode: 'preview' | 'process') => {
      const reason = dialogueStrategyDisabledReason(preferences.dialogueStrategy, budget.maxRecheckRounds)
      if (reason) throw new Error(reason)
      manuallySelectedJobRef.current = null
      setDetailRequest(0)
      const versionId = book.data?.active_version_id ?? null
      if (range.chapterId && (!estimate || estimateQuery.isFetching || estimateQuery.isError || selectedWindowIds.length === 0)) {
        throw new Error('请等待窗口预览完成并选择至少一个窗口')
      }
      const plannedWindows = range.chapterId && estimate?.windows?.length
        ? (estimate.windows ?? []).filter((window) => selectedWindowIds.includes(String(window.window_id)))
        : []
      if (range.chapterId && plannedWindows.length === 0) {
        throw new Error('没有选中当前章节的有效窗口，不能开始处理')
      }
      const selectedChapter = chapters.data?.find(chapter => chapter.id === range.chapterId)
      if (mode === 'process' && selectedChapter?.processing_status_override != null && versionId && bookId) {
        const saved = await setChapterProcessingStatus(bookId, selectedChapter.id, versionId, null)
        queryClient.setQueryData<ChapterOut[]>(queryKeys.chapters(bookId), previous =>
          previous?.map(chapter => chapter.id === saved.id ? saved : chapter))
      }
      const totalEstimated = plannedWindows.reduce((total, window) => total + Math.max(1, Number(window.estimated_tokens) || 1), 0)
      const allocate = (limit: number | null, estimated: number) =>
        limit === null ? null : Math.max(1, Math.floor(limit * estimated / totalEstimated))
      const selected = plannedWindows.length ? plannedWindows : [{ window_id: 'range', ordinal: 1, estimated_tokens: 1 }]
      const work: SingleWorkflow = {
        bookId: bookId!, versionId: versionId!, chapterId: range.chapterId,
        startCp: range.startCp, endCp: resolvedEnd, mode, concurrency, running: true, error: null,
        completeChapter: mode === 'process' && Boolean(range.chapterId) && (estimate?.windows ?? []).every(
          window => window.processing_status === 'completed' || selectedWindowIds.includes(String(window.window_id))),
        tasks: selected.map(window => ({
          windowId: String(window.window_id), ordinal: String(window.ordinal), job: null, error: null,
          input: {
            bookId: bookId!, mode, bookVersionId: versionId,
            range: { chapterId: range.chapterId, startCp: range.startCp, endCp: resolvedEnd, dialogueStrategy: preferences.dialogueStrategy },
            selectedWindowIds: plannedWindows.length ? [String(window.window_id)] : null,
            profileId: profileId || null, inferenceOptions: options, readingMode: PROCESSING_READING_MODE,
            visibleHorizonCp: null, runNow: true,
            budget: plannedWindows.length ? { ...budget,
              maxInputTokens: allocate(budget.maxInputTokens, Math.max(1, Number(window.estimated_tokens) || 1)),
              maxOutputTokens: allocate(budget.maxOutputTokens, Math.max(1, Number(window.estimated_tokens) || 1)) } : budget,
            idempotencyKey: freshIdempotencyKey(`${mode}:${bookId}:window`, JSON.stringify({ versionId, range, profileId, budget, windowId: window.window_id })),
          },
        })),
      }
      setError(null)
      setNotice(null)
      return runSingleWorkflow(work)
    },
    onSuccess: (job) => {
      selectAutomaticJob(job.id)
      setCurrentJob(job)
      setError(null)
      setNotice(job.state === 'COMPLETED'
        ? '任务完成：阅读标注已保存，返回阅读即可查看结果。'
        : `任务结束于 ${SINGLE_TASK_LABELS[job.state] ?? job.state}，请在任务面板查看原因。`)
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '创建任务失败'),
  })

  const syncCompletion = useMutation({
    mutationFn: async () => {
      if (!bookId || !range.chapterId || !book.data?.active_version_id) throw new Error('请先选择章节')
      await completeChapterProcessing(bookId, range.chapterId, book.data.active_version_id)
      reconcileCompletedChapter(bookId, range.chapterId)
      await queryClient.invalidateQueries({ queryKey: queryKeys.chapters(bookId) })
    },
    onSuccess: () => { setError(null); setNotice('章节完成状态已同步，未调用模型。') },
    onError: (reason: unknown) => setError(reason instanceof Error ? reason.message : '状态同步失败'),
  })

  const handleJobUpdate = useCallback(
    (job: JobDetailOut) => {
      setCurrentJob(job)
      setSingleTasks((tasks) => tasks.map((task) => task.job?.id === job.id ? { ...task, job } : task))
      if (!TERMINAL_JOB_STATES.has(job.state)) {
        return
      }
      setNotice(
        job.state === 'COMPLETED'
          ? '任务完成：阅读标注已保存，返回阅读即可查看结果。'
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
  const singleRunning = Boolean(singleProgress?.running) || jobMutation.isPending || singleTasks.some(
    (task) => task.job && !TERMINAL_JOB_STATES.has(task.job.state),
  ) || Boolean(currentJob && !TERMINAL_JOB_STATES.has(currentJob.state))
  const runBlockers: string[] = []
  const strategyReason = dialogueStrategyDisabledReason(preferences.dialogueStrategy, budget.maxRecheckRounds)
  if (strategyReason) runBlockers.push(strategyReason)
  if (singleRunning) runBlockers.push('当前单章任务尚未结束，请等待或在任务面板停止')
  if (!singleProgress && recentJobs.isPending) runBlockers.push('正在读取已有任务')
  if (recentJobs.isError) runBlockers.push('已有任务读取失败，请重新读取后再处理')
  if (!rangeValid) runBlockers.push('处理范围无效')
  if (profileId === '') runBlockers.push('未选择模型配置（见上方模型配置与本次思考设置）')
  if (rosterRequired && !rosterConfirmed) runBlockers.push('尚未确认本章人物（见第二步）')
  if (range.chapterId && (!estimate || estimateQuery.isFetching || estimateQuery.isError)) {
    runBlockers.push(estimateQuery.isError ? '窗口预览失败，请重新读取' : '正在读取窗口预览')
  }
  if (range.chapterId && estimate && !(estimate.windows ?? []).some((window) => selectedWindowIds.includes(String(window.window_id)))) {
    runBlockers.push('尚未选择要处理的窗口')
  }
  const runDisabled = runBlockers.length > 0 || jobMutation.isPending
  const selectedTask = singleTasks.find(task => task.job?.id === jobId)

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
        <nav className="ndr-book-nav" aria-label="本书导航">
          <Link to={`/books/${bookId}/read${returnChapterId ? `?chapterId=${encodeURIComponent(returnChapterId)}` : ''}`}>去阅读</Link>
          <Link to={`/books/${bookId}/characters${returnChapterId ? `?chapterId=${encodeURIComponent(returnChapterId)}` : ''}`}>全书人物</Link>
          <Link to={`/books/${bookId}/review`}>待确认队列</Link>
          <button type="button" onClick={() => setExportOpen(true)} data-testid="open-export">
            导出
          </button>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      {book.isError && (
        <ReadErrorNotice
          label="书籍读取失败"
          error={book.error}
          retrying={book.isFetching}
          onRetry={() => void book.refetch()}
        />
      )}
      {chapters.isError && (
        <ReadErrorNotice
          label="目录读取失败"
          error={chapters.error}
          retrying={chapters.isFetching}
          onRetry={() => void chapters.refetch()}
        />
      )}

      <section className="card">
        <h3>书籍预处理</h3>
        <p className="hint">检查章节名、分章边界和引号修复。导入时已自动检查，可在这里确认结果、调整或重试。</p>
        <Link className="ndr-button" to={`/books/${bookId}/preprocessing${returnChapterId ? `?chapterId=${encodeURIComponent(returnChapterId)}` : ''}`}>打开预处理</Link>
      </section>

      <section className="card ndr-step-card" data-testid="processing-mode-picker">
        <div className="ndr-step-heading">
          <div>
            <h3>选择处理方式</h3>
            <p className="hint">单章可挑选窗口试运行；批量会自动识别人物并按章节流水线处理。</p>
            <p className="hint">模型、复核数、并发数和额度自动保存，单章与批量共用，重新打开后仍保留。</p>
          </div>
        </div>
        <div className="ndr-radio-row">
          <label>
            <input
              type="radio"
              name="processing-mode"
              checked={processingMode === 'single'}
              disabled={batchProgress.running || singleRunning}
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
              disabled={batchProgress.running || singleRunning}
              onChange={() => setProcessingMode('batch')}
              data-testid="processing-mode-batch"
            />
            批量处理
          </label>
        </div>
      </section>

      <ThinkingSettings disabled={batchProgress.running || singleRunning} profiles={profiles.data ?? []}
        disabledReason="当前处理任务尚未结束；请等待完成或先停止任务，再切换处理方式及模型配置。"
        profileId={profileId} onProfileChange={setProfileId}
        profileTestId={processingMode === 'batch' ? 'batch-profile' : 'preview-profile'} />
      <DialogueStrategySettings value={preferences.dialogueStrategy} rounds={preferences.maxRecheckRounds}
        disabled={batchProgress.running || singleRunning}
        onChange={dialogueStrategy => setPreferences({ dialogueStrategy })} />
      <RosterRepairSettings preferences={preferences} onChange={setPreferences}
        disabled={batchProgress.running || singleRunning} />

      {processingMode === 'single' && !batchProgress.running && (
        <>

      <section className="card ndr-preview-controls ndr-step-card">
        <div className="ndr-step-heading">
          <span className="ndr-step-badge" aria-hidden="true">1</span>
          <div>
            <h3>选择处理范围</h3>
            <p className="hint">选择章节和对白窗口；模型配置使用上方的共用设置。</p>
          </div>
        </div>
        <RangePicker
          chapters={chapters.data ?? []}
          value={range}
          canonicalLengthCp={canonicalLengthCp}
          onChange={(next) => {
            setRange(next)
            if (next.chapterId !== range.chapterId || next.startCp !== range.startCp || next.endCp !== range.endCp) {
              selectionKeyRef.current = ''
              selectionTouchedRef.current = false
              setSelectedWindowIds([])
            }
            setNotice(null)
          }}
        />
        {range.chapterId && (
          <>
            {estimateQuery.isFetching && <p className="hint" role="status">正在生成窗口预览（不调用模型）…</p>}
            {estimateQuery.isError && <ReadErrorNotice label="窗口预览失败" error={estimateQuery.error}
              retrying={estimateQuery.isFetching} onRetry={() => void estimateQuery.refetch()} />}
            {estimate && (estimate.windows?.length ?? 0) > 0 && <WindowPicker
              windows={estimate.windows ?? []} selectedIds={selectedWindowIds}
              onChange={ids => { selectionTouchedRef.current = true; setSelectedWindowIds(ids) }} disabled={jobMutation.isPending || estimateQuery.isFetching}
            />}
            {estimate?.windows?.length && estimate.windows.every(window => window.processing_status === 'completed')
              && chapters.data?.some(chapter => chapter.id === range.chapterId && !chapter.dialogue_processed
                && chapter.start_cp === range.startCp && chapter.end_cp === resolvedEnd) ? <p className="hint">
                全部窗口已有标注，可直接同步章节状态，无需再次调用模型。
                <button type="button" disabled={jobMutation.isPending || syncCompletion.isPending || estimateQuery.isFetching}
                  title={jobMutation.isPending ? '窗口任务正在提交，请等待完成。' : syncCompletion.isPending ? '正在同步章节完成状态，请等待保存完成。' : estimateQuery.isFetching ? '正在读取窗口，请等待读取完成。' : undefined}
                  onClick={() => syncCompletion.mutate()}>同步章节完成状态（不调用模型）</button>
              </p> : null}
            {estimate && !estimateQuery.isFetching && (estimate.windows?.length ?? 0) === 0 && (
              <p className="hint">本章没有可处理的对白窗口。</p>
            )}
          </>
        )}
      </section>

      <CharacterRosterPanel
        step={2}
        bookId={bookId}
        bookVersionId={book.data?.active_version_id ?? null}
        chapterId={range.chapterId}
        profileId={profileId}
        inferenceOptions={options}
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
            onClick={() => void estimateQuery.refetch()}
            disabled={!rangeValid || estimateQuery.isFetching || jobMutation.isPending || Boolean(strategyReason)}
            data-testid="preview-estimate"
            title={!rangeValid ? '请先选择有效的处理范围。' : estimateQuery.isFetching ? '正在生成窗口预览，请等待完成。' : jobMutation.isPending ? '正在提交窗口任务，请等待完成。' : strategyReason ?? undefined}
          >
            重新预览窗口与估算（不调用模型）
          </button>
          <button
            type="button"
            onClick={() => jobMutation.mutate('preview')}
            disabled={runDisabled}
            data-testid="preview-run"
            title={runDisabled ? runBlockers.join('；') : undefined}
          >
            试运行预览（调用模型）
          </button>
          <button
            type="button"
            className="ndr-primary"
            onClick={() => jobMutation.mutate('process')}
            disabled={runDisabled}
            data-testid="preview-process"
            title={runDisabled ? runBlockers.join('；') : undefined}
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
        {!range.chapterId && estimateQuery.isError && (
          <ReadErrorNotice label="本地估算失败" error={estimateQuery.error}
            retrying={estimateQuery.isFetching} onRetry={() => void estimateQuery.refetch()} />
        )}
        {notice && (
          <p className="hint" data-testid="preview-notice">
            {notice}
          </p>
        )}
        <OperationTimer {...estimateClock} />
        {estimate && <EstimateSummary estimate={estimate} />}
      </section>

      {singleTasks.length > 0 && (
        <section className="card" data-testid="single-task-progress">
          <h2>单章窗口任务进度</h2>
          <p className="hint">提交后会先排队，再由后台执行。任务全部结束前不能新建任务；读取进度不会重新调用模型。</p>
          <CollapsibleBlock title="单章任务明细" summary={`共 ${singleTasks.length} 个窗口；失败 ${singleTasks.filter(task => task.error || task.job?.state === 'FAILED').length} 个`}>
          <div className="ndr-table-wrap"><table>
            <thead><tr><th>窗口</th><th>状态</th><th>详情</th></tr></thead>
            <tbody>{singleTasks.map((task) => (
              <tr key={task.windowId} data-testid={`single-task-${task.windowId}`}>
                <td>窗口 {task.ordinal}</td>
                <td>{task.job ? SINGLE_TASK_LABELS[task.job.state] ?? task.job.state : task.error ? '未派发' : '等待派发'}</td>
                <td>
                  {task.error || task.job?.last_error}
                  {task.job && <button type="button" aria-pressed={jobId === task.job.id}
                    className={jobId === task.job.id ? 'ndr-primary' : undefined}
                    aria-controls="single-task-details" onClick={() => showTaskDetails(task.job!.id)}>查看任务</button>}
                </td>
              </tr>
            ))}</tbody>
          </table></div>
          </CollapsibleBlock>
        </section>
      )}
      {jobId && (
        <section className="card" id="single-task-details" ref={taskDetailRef} tabIndex={-1}
          aria-labelledby="single-task-details-heading" data-testid="single-task-details" data-job-id={jobId}>
          <h2 id="single-task-details-heading">{selectedTask ? `窗口 ${selectedTask.ordinal} 任务详情` : '任务详情'}</h2>
          <p className="hint">查看本窗口的状态、耗时、调用次数与失败原因，不会重新调用模型。手动选择后不会被新派发的窗口切换。</p>
          <JobPanel key={jobId} jobId={jobId} onUpdate={handleJobUpdate} />
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
              title={runDisabled ? runBlockers.join('；') : undefined}
            >
              用当前预算重新处理此范围（可能计费）
            </button>
          </div>
        </section>
      )}
        </>
      )}

      {(processingMode === 'batch' || batchProgress.running || batchProgress.tasks.length > 0) && (
        <BatchProcessor
          bookId={bookId}
          initialChapterId={requestedChapterId}
          bookVersionId={book.data?.active_version_id}
          chapters={chapters.data ?? []}
          profiles={profiles.data ?? []}
          showConfiguration={processingMode === 'batch'}
          onFinished={() => {
            void queryClient.invalidateQueries({ queryKey: ['window-preview', bookId] })
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
