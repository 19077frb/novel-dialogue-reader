import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { annotationKeys, fetchAnnotations } from '../api/annotations'
import { fetchReviewQueue } from '../api/review'
import {
  fetchBook,
  fetchChapters,
  fetchContent,
  fetchQuotes,
  queryKeys,
  saveReadingProgress,
} from '../api/books'
import { ApiError } from '../api/client'
import type { ChapterOut, ContentNodeOut, ReadingMode } from '../api/types'
import { ChapterNavigation } from '../components/ChapterNavigation'
import {
  useBatchAnnotationRevisions,
  useBatchCatalogRevision,
  useBatchChapterProgress,
  useBatchMessage,
} from '../components/BatchProcessor'
import type { CandidateRange } from '../components/DocumentRenderer'
import { DocumentRenderer } from '../components/DocumentRenderer'
import { ExportDialog } from '../components/ExportDialog'
import { QuoteDetailDrawer } from '../components/QuoteDetailDrawer'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import { SpeakerLegend } from '../components/SpeakerLegend'

function ReaderBatchMessage({ bookId }: { bookId: string }) {
  const message = useBatchMessage(bookId)
  return message ? <p className="hint" data-testid="reader-batch-progress">{message}</p> : null
}

/** 找到视口内第一个节点对应的起点；用于保存阅读位置（纯函数，便于测试）。 */
export function findCurrentStartCp(nodes: HTMLElement[]): number | null {
  if (nodes.length === 0) return null
  for (const node of nodes) {
    const top = node.getBoundingClientRect().top
    const raw = node.dataset.startCp
    if (top >= 0 && raw !== undefined) return Number(raw)
  }
  const last = nodes[nodes.length - 1]?.dataset.startCp
  return last !== undefined ? Number(last) : null
}

/**
 * 阅读页：导入后即可阅读原文，并与预览页共用**同一套标注投影**
 * （`GET /api/books/{id}/annotations`）。翻页只查投影，不触发任何推理。
 */
export default function ReaderPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const queryClient = useQueryClient()
  const batchChapterProgress = useBatchChapterProgress(bookId)
  const annotationRevisions = useBatchAnnotationRevisions(bookId)
  const catalogRevision = useBatchCatalogRevision(bookId)
  const documentRef = useRef<HTMLDivElement>(null)
  const lastSavedRef = useRef<number | null>(null)
  const [chapterId, setChapterId] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [pages, setPages] = useState<ContentNodeOut[][]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [showCandidates, setShowCandidates] = useState(true)
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [readingModeOverride, setReadingModeOverride] = useState<ReadingMode | null>(null)
  const [selectedQuote, setSelectedQuote] = useState<{ quoteId: string; reviewItemId: string | null } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)

  const book = useQuery({
    queryKey: queryKeys.book(bookId ?? ''),
    queryFn: ({ signal }) => fetchBook(bookId as string, signal),
    enabled: Boolean(bookId),
  })

  const chapters = useQuery({
    queryKey: queryKeys.chapters(bookId ?? ''),
    queryFn: ({ signal }) => fetchChapters(bookId as string, signal),
    enabled: Boolean(bookId) && book.isSuccess,
  })

  useEffect(() => {
    if (!bookId || catalogRevision === 0) return
    const timer = window.setTimeout(() => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.chapters(bookId), exact: true })
    }, 400)
    return () => window.clearTimeout(timer)
  }, [catalogRevision, bookId, queryClient])

  const content = useQuery({
    queryKey: queryKeys.content(bookId ?? '', chapterId, cursor),
    queryFn: ({ signal }) =>
      fetchContent(bookId as string, { chapterId, cursor, limit: 500 }, signal),
    enabled: Boolean(bookId) && chapterId !== null,
  })

  // 候选引语（扫描器结果，不含说话人判断）
  const quotes = useQuery({
    queryKey: queryKeys.quotes(bookId ?? '', chapterId),
    queryFn: ({ signal }) => fetchQuotes(bookId as string, { chapterId, limit: 500 }, signal),
    enabled: Boolean(bookId) && chapterId !== null,
  })

  // 默认章节：书签所在章节（没有书签就用第一章）。
  useEffect(() => {
    if (!book.data || !chapters.data || chapterId !== null) return
    const position = book.data.read_position_cp
    const match =
      chapters.data.find((chapter) => position >= chapter.start_cp && position < chapter.end_cp) ??
      chapters.data[0]
    setChapterId(match ? match.id : null)
  }, [book.data, chapters.data, chapterId])

  useEffect(() => {
    setPages([])
    setCursor(null)
    lastSavedRef.current = null
  }, [chapterId])

  useEffect(() => {
    if (!content.data) return
    setPages((previous) => (cursor ? [...previous, content.data.nodes] : [content.data.nodes]))
  }, [content.data, cursor])

  const nodes = useMemo(() => pages.flat(), [pages])
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
  const activeChapter = chapters.data?.find((chapter) => chapter.id === chapterId) ?? null
  const readingMode: ReadingMode = readingModeOverride ?? book.data?.reading_mode ?? 'initial'

  // 初读 horizon：本章末端。
  // 后文才出现的证据不会提前着色，也不会提前把两个声音合成同一个颜色。
  const visibleHorizonCp = readingMode === 'initial' ? activeChapter?.end_cp ?? null : null

  // 标注投影：只读查询（不写库、不调用模型）。
  const annotations = useQuery({
    queryKey: annotationKeys.range(
      bookId ?? '',
      activeChapter?.start_cp ?? 0,
      activeChapter?.end_cp ?? 0,
      readingMode,
      visibleHorizonCp,
    ),
    queryFn: ({ signal }) =>
      fetchAnnotations(
        bookId as string,
        {
          startCp: activeChapter?.start_cp ?? 0,
          endCp: activeChapter?.end_cp ?? 0,
          readingMode,
          visibleHorizonCp,
        },
        signal,
      ),
    enabled:
      Boolean(bookId) &&
      activeChapter !== null &&
      activeChapter.end_cp > activeChapter.start_cp,
  })
  const activeAnnotationRevision = activeChapter
    ? annotationRevisions[activeChapter.id] ?? 0
    : 0
  useEffect(() => {
    if (!bookId || !activeChapter || activeAnnotationRevision === 0) return
    const timer = window.setTimeout(() => {
      void queryClient.invalidateQueries({
        queryKey: annotationKeys.range(
          bookId,
          activeChapter.start_cp,
          activeChapter.end_cp,
          readingMode,
          visibleHorizonCp,
        ),
        exact: true,
      })
    }, 300)
    return () => window.clearTimeout(timer)
  }, [
    activeAnnotationRevision,
    activeChapter,
    bookId,
    queryClient,
    readingMode,
    visibleHorizonCp,
  ])
  const annotationItems = showAnnotations ? annotations.data?.items ?? [] : []

  // 待确认数量（只读）：阅读页顶部提示，点击进入队列页。
  const pending = useQuery({
    queryKey: ['review-items', bookId ?? '', 'pending-count'],
    queryFn: ({ signal }) =>
      fetchReviewQueue(bookId as string, { queueStatus: 'PENDING', limit: 1 }, signal),
    enabled: Boolean(bookId),
  })

  const progress = useMutation({
    mutationFn: (input: {
      readPositionCp: number
      readingMode: ReadingMode
      expectedVersion: number
    }) =>
      saveReadingProgress(bookId as string, {
        bookVersionId: book.data?.active_version_id ?? '',
        readPositionCp: input.readPositionCp,
        readingMode: input.readingMode,
        expectedVersion: input.expectedVersion,
      }),
    onSuccess: () => setNotice(null),
    onError: async (error: unknown) => {
      if (error instanceof ApiError && error.code === 'VERSION_CONFLICT') {
        // 服务端版本更新了：刷新书籍后按最新版本重试一次，绝不覆盖较新的写入。
        const fresh = await book.refetch()
        if (fresh.data) {
          progress.mutate({
            readPositionCp: fresh.data.read_position_cp,
            readingMode: fresh.data.reading_mode,
            expectedVersion: fresh.data.version,
          })
        }
        return
      }
      setNotice('阅读位置保存失败，稍后会自动重试。')
    },
  })

  const persistPosition = useCallback(
    (readPositionCp: number) => {
      if (!book.data) return
      if (lastSavedRef.current === readPositionCp) return
      lastSavedRef.current = readPositionCp
      progress.mutate({
        readPositionCp,
        readingMode: book.data.reading_mode,
        expectedVersion: book.data.version,
      })
    },
    [book.data, progress],
  )

  const handleScroll = useCallback(() => {
    const container = documentRef.current
    if (!container) return
    const elements = Array.from(
      container.querySelectorAll<HTMLElement>('[data-node-id]'),
    ) as HTMLElement[]
    const startCp = findCurrentStartCp(elements)
    if (startCp !== null) persistPosition(startCp)
  }, [persistPosition])

  const handleChapterSelect = useCallback(
    (chapter: ChapterOut) => {
      setChapterId(chapter.id)
      setNotice(null)
      persistPosition(chapter.start_cp)
    },
    [persistPosition],
  )

  const focusQuote = useCallback((quoteId: string | null | undefined) => {
    if (!quoteId) return
    const target = documentRef.current?.querySelector(`[data-quote-id="${quoteId}"]`)
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [])

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  const encodingLabel = book.data?.active_version
    ? book.data.active_version.encoding === 'xml'
      ? 'EPUB'
      : book.data.active_version.encoding
    : ''

  return (
    <div className="ndr-page ndr-reader">
      <header className="ndr-reader-header card ndr-page-header">
        <div>
          <h2>{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            {book.data?.format ?? ''}
            {encodingLabel ? ` · ${encodingLabel}` : ''}
            {book.data ? ` · 书签位置 ${book.data.read_position_cp}` : ''}
          </p>
        </div>
        <nav className="ndr-preview-nav">
          <Link to={`/books/${bookId}/review`} data-testid="reader-review-link">
            待确认 {pending.data?.counts.targets_by_status?.PENDING ?? 0} 项
          </Link>
          <Link to={`/books/${bookId}/preview`}>预览与处理</Link>
          <button type="button" onClick={() => setExportOpen(true)} data-testid="open-export">
            导出
          </button>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      {notice && (
        <p className="hint" data-testid="reader-notice">
          {notice}
        </p>
      )}
      {book.isError && (
        <ReadErrorNotice
          label="书籍读取失败"
          error={book.error}
          retrying={book.isFetching}
          onRetry={() => void book.refetch()}
          testId="book-read-error"
        />
      )}

      <div className="ndr-reader-body">
        <aside className="card ndr-reader-sidebar">
          {chapters.isSuccess && (
            <ChapterNavigation
              chapters={chapters.data}
              activeChapterId={chapterId}
              onSelect={handleChapterSelect}
              processingStates={batchChapterProgress}
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
          <ReaderBatchMessage bookId={bookId} />
          {chapters.isPending && <p className="hint">正在读取目录…</p>}
        </aside>

        <section className="card ndr-reader-content" ref={documentRef} onScroll={handleScroll}>
          {chapterId === null && <p className="hint">这本书没有可显示的章节。</p>}
          {content.isPending && chapterId !== null && <p className="hint">正在读取正文…</p>}
          {content.isError && (
            <ReadErrorNotice
              label="正文读取失败"
              error={content.error}
              retrying={content.isFetching}
              onRetry={() => void content.refetch()}
            />
          )}
          {nodes.length > 0 && (
            <>
              <h1 className="ndr-chapter-heading">{activeChapter?.title ?? '正文'}</h1>
              <div className="ndr-quote-legend">
                <label>
                  <input
                    type="checkbox"
                    checked={showCandidates}
                    onChange={(event) => setShowCandidates(event.target.checked)}
                    data-testid="toggle-candidates"
                  />
                  候选引语 {candidates.length} 条（扫描器结果，尚未判定说话人）
                  {quotes.data?.next_cursor ? '（本章还有更多候选未加载）' : ''}
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={showAnnotations}
                    onChange={(event) => setShowAnnotations(event.target.checked)}
                    data-testid="toggle-annotations"
                  />
                  标注 {annotations.data?.items?.length ?? 0} 条（颜色/编号来自后端投影）
                </label>
                <label>
                  阅读模式
                  <select
                    value={readingMode}
                    onChange={(event) =>
                      setReadingModeOverride(event.target.value as ReadingMode)
                    }
                    data-testid="reader-reading-mode"
                  >
                    <option value="initial">初读</option>
                    <option value="reread">重读</option>
                  </select>
                </label>
              </div>
              {readingMode === 'initial' && annotations.data && (
                <p className="hint" data-testid="reader-horizon">
                  初读：只显示到位置 {annotations.data.visible_horizon_cp ?? '—'} 为止的证据
                  {annotations.data.counts.withheld > 0
                    ? `（${annotations.data.counts.withheld} 条后文证据暂不显示）`
                    : ''}
                  {annotations.data.identity_reverts > 0
                    ? `；${annotations.data.identity_reverts} 处身份合并在后文才揭示`
                    : ''}
                </p>
              )}
              {annotations.isError && (
                <p className="hint" data-testid="annotations-error">
                  标注投影读取失败（原文不受影响）。
                </p>
              )}
              {showAnnotations && (annotations.data?.legend?.length ?? 0) > 0 && (
                <SpeakerLegend
                  legend={annotations.data?.legend ?? []}
                  onFocus={(item) => focusQuote(item.first_quote_id)}
                />
              )}
              <DocumentRenderer
                bookId={bookId}
                nodes={nodes}
                candidates={showCandidates ? candidates : []}
                annotations={annotationItems}
                onQuoteClick={(quoteId) => setSelectedQuote({ quoteId, reviewItemId: null })}
              />
            </>
          )}
          {content.data?.next_cursor && (
            <button
              type="button"
              onClick={() => setCursor(content.data?.next_cursor ?? null)}
              data-testid="load-more"
            >
              加载本章后续内容
            </button>
          )}
        </section>
      </div>

      <ExportDialog
        bookId={bookId}
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        chapters={chapters.data ?? []}
        readPositionCp={book.data?.read_position_cp ?? 0}
      />

      <QuoteDetailDrawer
        quoteId={selectedQuote?.quoteId ?? null}
        reviewItemId={selectedQuote?.reviewItemId ?? null}
        onClose={() => setSelectedQuote(null)}
        onCorrected={() => void pending.refetch()}
      />
    </div>
  )
}
