import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { annotationKeys, fetchAnnotations } from '../api/annotations'
import { fetchReviewQueue } from '../api/review'
import {
  fetchBook,
  fetchChapters,
  fetchContent,
  fetchQuotes,
  queryKeys,
} from '../api/books'
import { addBookmark, bookmarkKey } from '../api/bookmarks'
import { BookmarkList } from '../components/BookmarkList'
import { useReadingProgress } from '../hooks/useReadingProgress'
import { getGeneralSettings } from '../settings/preferences'
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
import { AutomaticProcessing } from '../components/AutomaticProcessing'

function ReaderBatchMessage({ bookId }: { bookId: string }) {
  const message = useBatchMessage(bookId)
  return message ? <p className="hint" data-testid="reader-batch-progress">{message}</p> : null
}

/** 找到视口内第一个节点对应的起点；用于保存阅读位置（纯函数，便于测试）。 */
export function findCurrentStartCp(nodes: HTMLElement[], clipTop = 0): number | null {
  if (nodes.length === 0) return null
  for (const node of nodes) {
    const rect = node.getBoundingClientRect()
    const top = rect.top
    const raw = node.dataset.startCp
    if ((top >= clipTop || rect.bottom > clipTop) && raw !== undefined) return Number(raw)
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
  const [searchParams] = useSearchParams()
  const requestedChapterId = searchParams.get('chapterId')
  const requestedPosition = searchParams.get('positionCp')
  const [resumeCp, setResumeCp] = useState<number | null>(null)
  const restoredRef = useRef<string | null>(null)
  const initializedChapterRef = useRef<string | null>(null)
  const queryClient = useQueryClient()
  const batchChapterProgress = useBatchChapterProgress(bookId)
  const annotationRevisions = useBatchAnnotationRevisions(bookId)
  const catalogRevision = useBatchCatalogRevision(bookId)
  const documentRef = useRef<HTMLDivElement>(null)
  const [chapterId, setChapterId] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [pages, setPages] = useState<ContentNodeOut[][]>([])
  const [notice, setNotice] = useState<string | null>(null)
  const [showCandidates, setShowCandidates] = useState(() => getGeneralSettings().showCandidates)
  const [showAnnotations, setShowAnnotations] = useState(() => getGeneralSettings().showAnnotations)
  const [readingModeOverride, setReadingModeOverride] = useState<ReadingMode | null>(null)
  const [selectedQuote, setSelectedQuote] = useState<{ quoteId: string; reviewItemId: string | null } | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [sidebarTab, setSidebarTab] = useState<'chapters' | 'bookmarks'>('chapters')

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

  // 明确跳转的章节优先于最后阅读位置；只初始化一次，不覆盖用户后续目录选择。
  useEffect(() => {
    const key = JSON.stringify([bookId, requestedChapterId, requestedPosition])
    if (!book.data || !chapters.data?.length || initializedChapterRef.current === key) return
    const position = !getGeneralSettings().resumeReading || (book.data.read_position_version_id && book.data.read_position_version_id !== book.data.active_version_id) ? 0 : book.data.read_position_cp
    const match =
      chapters.data.find((chapter) => chapter.id === requestedChapterId) ??
      chapters.data.find((chapter) => position >= chapter.start_cp && position < chapter.end_cp) ??
      chapters.data[0]
    initializedChapterRef.current = key
    setChapterId(match ? match.id : null)
    const target = requestedPosition !== null ? Number(requestedPosition) : requestedChapterId ? null : position
    setResumeCp(match && target !== null && Number.isInteger(target) && target >= match.start_cp && target < match.end_cp ? target : null)
    restoredRef.current = null
  }, [bookId, requestedChapterId, requestedPosition, book.data, chapters.data])

  useEffect(() => {
    setPages([])
    setCursor(null)
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
  const persist = useReadingProgress(bookId ?? '', book.data, setNotice)
  const bookmark = useMutation({ mutationFn: ({ cp, note }: { cp: number; note: string }) => addBookmark(bookId!, {
    book_version_id: book.data!.active_version_id!, chapter_id: chapterId!, position_cp: cp, note,
  }), onSuccess: () => { setNotice('书签已添加。'); void queryClient.invalidateQueries({ queryKey: bookmarkKey(bookId!) }) },
  onError: (error) => setNotice(`添加书签失败：${error.message}`) })

  useEffect(() => {
    const key = `${chapterId}:${resumeCp}`
    if (resumeCp === null || !nodes.length || restoredRef.current === key) return
    const paragraph = Array.from(documentRef.current?.querySelectorAll<HTMLElement>('[data-paragraph-start]') ?? [])
      .find(item => Number(item.dataset.paragraphStart) <= resumeCp && Number(item.dataset.paragraphEnd) > resumeCp)
    const target = paragraph ?? Array.from(documentRef.current?.querySelectorAll<HTMLElement>('[data-node-id]') ?? [])
      .find(node => Number(node.dataset.endCp) > resumeCp && Number(node.dataset.startCp) <= resumeCp)
    if (target) { restoredRef.current = key; target.scrollIntoView?.({ block: 'start' }) }
    else if (content.data?.chapter_id === chapterId && content.data.next_cursor && !content.isFetching) {
      setCursor(content.data.next_cursor)
    }
  }, [nodes, chapterId, resumeCp, content.data, content.isFetching])

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

  const handleScroll = useCallback(() => {
    const container = documentRef.current
    if (!container) return
    const bounds = container.getBoundingClientRect()
    if (bounds.top >= window.innerHeight || bounds.bottom < 0) return
    const elements = Array.from(
      container.querySelectorAll<HTMLElement>('[data-node-id]'),
    ) as HTMLElement[]
    const startCp = findCurrentStartCp(elements, Math.max(0, bounds.top))
    if (startCp !== null && !content.isFetching) persist(startCp, readingMode)
  }, [persist, readingMode, content.isFetching])

  useEffect(() => {
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [handleScroll])

  const handleChapterSelect = useCallback(
    (chapter: ChapterOut) => {
      setChapterId(chapter.id)
      setResumeCp(null)
      setNotice(null)
      persist(chapter.start_cp, readingMode, true)
    },
    [persist, readingMode],
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
            {book.data ? ` · 最后读到：${book.data.last_read_chapter_title ?? activeChapter?.title ?? '尚未记录'}` : ''}
          </p>
        </div>
        <nav className="ndr-book-nav" aria-label="本书导航">
          <Link to={`/books/${bookId}/preview${chapterId ? `?chapterId=${encodeURIComponent(chapterId)}` : ''}`}>预览与处理</Link>
          <Link to={`/books/${bookId}/characters${chapterId ? `?chapterId=${encodeURIComponent(chapterId)}` : ''}`}>全书人物</Link>
          <Link to={`/books/${bookId}/review`} data-testid="reader-review-link">
            待确认 {pending.data?.counts.targets_by_status?.PENDING ?? 0} 项
          </Link>
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
          <div className="ndr-sidebar-tabs" role="tablist" aria-label="阅读导航" onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const next = event.key === 'Home' ? 'chapters' : event.key === 'End' ? 'bookmarks' : sidebarTab === 'chapters' ? 'bookmarks' : 'chapters'
            setSidebarTab(next)
            document.getElementById(next === 'chapters' ? 'chapter-tab' : 'bookmark-tab')?.focus()
          }}>
            <button role="tab" id="chapter-tab" aria-selected={sidebarTab === 'chapters'} aria-controls="chapter-panel"
              tabIndex={sidebarTab === 'chapters' ? 0 : -1}
              className={sidebarTab === 'chapters' ? 'ndr-primary' : ''} onClick={() => setSidebarTab('chapters')}>目录</button>
            <button role="tab" id="bookmark-tab" aria-selected={sidebarTab === 'bookmarks'} aria-controls="bookmark-panel"
              tabIndex={sidebarTab === 'bookmarks' ? 0 : -1}
              className={sidebarTab === 'bookmarks' ? 'ndr-primary' : ''} onClick={() => setSidebarTab('bookmarks')}>书签</button>
          </div>
          <div id="chapter-panel" role="tabpanel" aria-labelledby="chapter-tab" hidden={sidebarTab !== 'chapters'}>
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
          <AutomaticProcessing bookId={bookId} bookVersionId={book.data?.active_version_id} chapterId={chapterId} />
          {chapters.isPending && <p className="hint">正在读取目录…</p>}
          </div>
          {sidebarTab === 'bookmarks' && <div id="bookmark-panel" role="tabpanel" aria-labelledby="bookmark-tab">
            <p className="hint">点击正文段落旁的 ☆ 保存书签。</p>
            <BookmarkList bookId={bookId} activeVersionId={book.data?.active_version_id} />
            <Link className="ndr-button" to={`/books/${bookId}/bookmarks`}>打开独立书签页</Link>
          </div>}
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
                  候选引语 {candidates.length} 条（检测到的引号内容，尚未判定说话人）
                  {quotes.data?.next_cursor ? '（本章还有更多候选未加载）' : ''}
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={showAnnotations}
                    onChange={(event) => setShowAnnotations(event.target.checked)}
                    data-testid="toggle-annotations"
                  />
                  本章已标注 {annotations.data?.items?.length ?? 0} 句对白
                </label>
                <label>
                  阅读模式
                  <select
                    value={readingMode}
                    onChange={(event) =>
                      { const mode = event.target.value as ReadingMode; setReadingModeOverride(mode); persist(book.data?.read_position_cp ?? activeChapter?.start_cp ?? 0, mode, true) }
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
                  对白标注读取失败（原文不受影响）。
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
                bookmarkPending={bookmark.isPending}
                onBookmark={(cp, text) => {
                  if (!activeChapter || !book.data?.active_version_id || bookmark.isPending) return
                  const note = window.prompt(`保存这段话为书签：${text.trim().slice(0, 80)}\n备注（可留空，最多 512 字）`, '')
                  if (note !== null) bookmark.mutate({ cp, note: note.slice(0, 512) })
                }}
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
