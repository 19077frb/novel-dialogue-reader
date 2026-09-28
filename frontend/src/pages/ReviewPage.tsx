/**
 * 待确认队列页。
 *
 * 门槛：用户不写代码就能完成全部人工确认；**队列清空不等于全部识别正确**
 * （这里只显示「已知待确认项」，未知/暂定/过期数量在统计里单独列出）。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { fetchBook, fetchChapters, fetchGaps, fetchQuotes, queryKeys } from '../api/books'
import { fetchReviewQueue, submitGapCorrection, type ReviewFilters } from '../api/review'
import type {
  GapDecision,
  GapOut,
  ReviewQueueStatus,
  ReviewReason,
  ReviewQueueResponse,
} from '../api/types'
import { GapDecisionControls } from '../components/GapDecisionControls'
import { QuoteDetailDrawer } from '../components/QuoteDetailDrawer'

const REASONS: ReviewReason[] = [
  'LOW_CONFIDENCE',
  'AMBIGUOUS_SPEAKER',
  'UNKNOWN_SPEAKER',
  'POSSIBLE_NEW_SPEAKER',
  'SCENE_BOUNDARY',
  'STALE_DEPENDENCY',
  'USER_FLAGGED',
  'OTHER',
]

export default function ReviewPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const queryClient = useQueryClient()
  const [chapterId, setChapterId] = useState('')
  const [reason, setReason] = useState<'' | ReviewReason>('')
  const [queueStatus, setQueueStatus] = useState<'' | ReviewQueueStatus>('PENDING')
  const [cursor, setCursor] = useState<string | null>(null)
  const [pages, setPages] = useState<ReviewQueueResponse[]>([])
  const [selected, setSelected] = useState<{ quoteId: string; reviewItemId: string } | null>(null)
  const [openGapId, setOpenGapId] = useState<string | null>(null)

  const filters = useMemo<ReviewFilters>(
    () => ({
      chapterId: chapterId || null,
      reason: reason || '',
      queueStatus: queueStatus || '',
      limit: 50,
    }),
    [chapterId, reason, queueStatus],
  )

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
  const quotes = useQuery({
    queryKey: queryKeys.quotes(bookId ?? '', null),
    queryFn: ({ signal }) => fetchQuotes(bookId as string, { limit: 500 }, signal),
    enabled: Boolean(bookId),
  })
  const gaps = useQuery({
    queryKey: ['gaps', bookId ?? ''],
    queryFn: ({ signal }) => fetchGaps(bookId as string, { limit: 200 }, signal),
    enabled: Boolean(bookId),
  })
  const queue = useQuery({
    queryKey: ['review-items', bookId ?? '', filters, cursor],
    queryFn: ({ signal }) =>
      fetchReviewQueue(bookId as string, { ...filters, cursor }, signal),
    enabled: Boolean(bookId),
  })

  useEffect(() => {
    setPages([])
    setCursor(null)
  }, [filters])

  useEffect(() => {
    if (!queue.data) return
    setPages((previous) => (cursor ? [...previous, queue.data] : [queue.data]))
  }, [queue.data, cursor])

  const quoteText = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of quotes.data?.items ?? []) map.set(item.quote_id, item.delimited_text)
    return map
  }, [quotes.data])
  const gapById = useMemo(() => {
    const map = new Map<string, GapOut>()
    for (const item of gaps.data?.items ?? []) map.set(item.gap_id, item)
    return map
  }, [gaps.data])

  const items = pages.flatMap((page) => page.items ?? [])
  const counts = pages[0]?.counts

  const decision = useMutation({
    mutationFn: (input: { gapId: string; value: GapDecision }) =>
      submitGapCorrection(input.gapId, { decision: input.value }),
    onSuccess: () => {
      setOpenGapId(null)
      void queryClient.invalidateQueries({ queryKey: ['review-items'] })
      void queryClient.invalidateQueries({ queryKey: ['annotations'] })
    },
  })

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  return (
    <div className="ndr-page ndr-review">
      <header className="ndr-reader-header card">
        <div>
          <h2>待确认队列：{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            这里只列出**已知的待确认项**。队列清空不等于全部识别正确：
            过滤条件与「未知/暂定/过期」数量都要一起看。
          </p>
        </div>
        <nav className="ndr-preview-nav">
          <Link to={`/books/${bookId}/read`}>去阅读</Link>
          <Link to={`/books/${bookId}/preview`}>预览与处理</Link>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      <section className="card ndr-review-filters">
        <label>
          章节
          <select
            value={chapterId}
            onChange={(event) => setChapterId(event.target.value)}
            data-testid="review-chapter"
          >
            <option value="">全部章节</option>
            {(chapters.data ?? []).map((chapter) => (
              <option key={chapter.id} value={chapter.id}>
                {chapter.ordinal + 1}. {chapter.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          原因
          <select
            value={reason}
            onChange={(event) => setReason(event.target.value as '' | ReviewReason)}
            data-testid="review-reason"
          >
            <option value="">全部原因</option>
            {REASONS.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label>
          状态
          <select
            value={queueStatus}
            onChange={(event) => setQueueStatus(event.target.value as '' | ReviewQueueStatus)}
            data-testid="review-status"
          >
            <option value="PENDING">待确认</option>
            <option value="DEFERRED">已跳过（延后）</option>
            <option value="RESOLVED">已解决</option>
            <option value="">全部状态</option>
          </select>
        </label>
        {counts && (
          <p className="hint" data-testid="review-counts">
            共 {counts.total} 项：
            {Object.entries(counts.by_status ?? {})
              .map(([key, value]) => `${key} ${value}`)
              .join(' · ')}
          </p>
        )}
      </section>

      <section className="card">
        {queue.isPending && <p className="hint">正在读取队列…</p>}
        {queue.isError && <p className="status-error">队列读取失败。</p>}
        {queue.isSuccess && items.length === 0 && (
          <p className="hint" data-testid="review-empty">
            当前筛选条件下没有待确认项。注意：这**不等于**整本书已完全确认——请同时查看
            「未知/暂定/过期」数量，或调整筛选条件。
          </p>
        )}
        <ul className="ndr-review-list" data-testid="review-list">
          {items.map((item) => {
            const quoteId = item.quote_id
            const gap = item.gap_id ? gapById.get(item.gap_id) : undefined
            return (
              <li key={item.id} data-testid="review-item" data-review-id={item.id}>
                <div className="ndr-review-item-main">
                  <span className="ndr-badge">{item.target_type}</span>
                  <span className="ndr-badge">{item.reason}</span>
                  <span className="ndr-badge">{item.queue_status}</span>
                  <span className="ndr-review-text">
                    {quoteId ? quoteText.get(quoteId) ?? '（对白）' : gap?.narration ?? '（Gap）'}
                  </span>
                </div>
                <div className="ndr-review-item-actions">
                  {quoteId && (
                    <button
                      type="button"
                      onClick={() => setSelected({ quoteId, reviewItemId: item.id })}
                      data-testid="review-open"
                    >
                      查看并确认
                    </button>
                  )}
                  {item.gap_id && gap && (
                    <button
                      type="button"
                      onClick={() =>
                        setOpenGapId(openGapId === item.gap_id ? null : item.gap_id ?? null)
                      }
                      data-testid="review-open-gap"
                    >
                      {openGapId === item.gap_id ? '收起' : '处理场景边界'}
                    </button>
                  )}
                </div>
                {item.gap_id && gap && openGapId === item.gap_id && (
                  <GapDecisionControls
                    gap={gap}
                    busy={decision.isPending}
                    onDecide={(value) => decision.mutate({ gapId: gap.gap_id, value })}
                  />
                )}
              </li>
            )
          })}
        </ul>
        {pages[pages.length - 1]?.next_cursor && (
          <button
            type="button"
            onClick={() => setCursor(pages[pages.length - 1]?.next_cursor ?? null)}
            data-testid="review-load-more"
          >
            加载更多
          </button>
        )}
      </section>

      <QuoteDetailDrawer
        quoteId={selected?.quoteId ?? null}
        reviewItemId={selected?.reviewItemId ?? null}
        onClose={() => setSelected(null)}
        onCorrected={() => {
          void queryClient.invalidateQueries({ queryKey: ['review-items'] })
        }}
      />
    </div>
  )
}