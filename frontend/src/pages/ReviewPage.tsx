/**
 * 待确认队列页。
 *
 * 门槛：用户不写代码就能完成全部人工确认；**队列清空不等于全部识别正确**
 * （这里只显示「已知待确认项」，未知/暂定/过期数量在统计里单独列出）。
 */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { CollapsibleBlock } from '../components/CollapsibleBlock'
import { PaginatedItems } from '../components/ListPagination'
import { ReadErrorNotice } from '../components/ReadErrorNotice'

import { fetchBook, fetchChapters, fetchGaps, queryKeys } from '../api/books'
import { cleanupDependencyReviews, fetchReviewQueue, submitGapCorrection, type ReviewFilters } from '../api/review'
import type {
  GapDecision,
  GapOut,
  ReviewItemOut,
  ReviewQueueStatus,
  ReviewReason,
} from '../api/types'
import { GapDecisionControls } from '../components/GapDecisionControls'
import { QuoteDetailDrawer } from '../components/QuoteDetailDrawer'

const REASONS: ReviewReason[] = [
  'MODEL_OUTPUT_WARNING',
  'LOW_CONFIDENCE',
  'AMBIGUOUS_SPEAKER',
  'UNKNOWN_SPEAKER',
  'UNKNOWN_QUOTE_KIND',
  'UNKNOWN_QUOTE_SOURCE',
  'POSSIBLE_NEW_SPEAKER',
  'SCENE_BOUNDARY',
  'STALE_DEPENDENCY',
  'USER_FLAGGED',
  'OTHER',
]

const REASON_LABELS: Record<ReviewReason, string> = {
  MODEL_OUTPUT_WARNING: '模型结果校验警告',
  LOW_CONFIDENCE: '置信度低',
  AMBIGUOUS_SPEAKER: '说话人有歧义',
  UNKNOWN_SPEAKER: '无法确定说话人',
  UNKNOWN_QUOTE_KIND: '表达类型待确认',
  UNKNOWN_QUOTE_SOURCE: '引用来源待确认',
  POSSIBLE_NEW_SPEAKER: '可能是新说话人',
  SCENE_BOUNDARY: '场景边界待确认',
  STALE_DEPENDENCY: '人物或场景调整后需复核',
  USER_FLAGGED: '用户标记',
  OTHER: '其他',
}

const STATUS_LABELS: Record<ReviewQueueStatus, string> = {
  PENDING: '待确认',
  DEFERRED: '已延后',
  RESOLVED: '已解决',
}

interface GroupedReviewItem {
  item: ReviewItemOut
  reasons: ReviewReason[]
  statuses: ReviewQueueStatus[]
}

function groupReviewItems(items: ReviewItemOut[]): GroupedReviewItem[] {
  const grouped = new Map<string, GroupedReviewItem>()
  for (const item of items) {
    const key = item.quote_id ? `quote:${item.quote_id}` : item.gap_id ? `gap:${item.gap_id}` : item.id
    const existing = grouped.get(key)
    if (!existing) {
      grouped.set(key, { item, reasons: [item.reason], statuses: [item.queue_status] })
      continue
    }
    if (!existing.reasons.includes(item.reason)) existing.reasons.push(item.reason)
    if (!existing.statuses.includes(item.queue_status)) existing.statuses.push(item.queue_status)
    if (item.queue_status === 'PENDING' && existing.item.queue_status !== 'PENDING') {
      existing.item = item
    }
  }
  return [...grouped.values()]
}

export default function ReviewPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const queryClient = useQueryClient()
  const [chapterId, setChapterId] = useState('')
  const [reason, setReason] = useState<'' | ReviewReason>('')
  const [queueStatus, setQueueStatus] = useState<'' | ReviewQueueStatus>('PENDING')
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
  const gaps = useQuery({
    queryKey: ['gaps', bookId ?? ''],
    queryFn: ({ signal }) => fetchGaps(bookId as string, { limit: 200 }, signal),
    enabled: Boolean(bookId),
  })
  const queue = useInfiniteQuery({
    queryKey: ['review-items', bookId ?? '', filters],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      fetchReviewQueue(bookId as string, { ...filters, cursor: pageParam }, signal),
    getNextPageParam: page => page.next_cursor ?? undefined,
    enabled: Boolean(bookId),
  })
  const pages = queue.data?.pages ?? []

  const gapById = useMemo(() => {
    const map = new Map<string, GapOut>()
    for (const item of gaps.data?.items ?? []) map.set(item.gap_id, item)
    return map
  }, [gaps.data])

  const items = pages.flatMap((page) => page.items ?? [])
  const groupedItems = groupReviewItems(items)
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

  const cleanup = useMutation({
    mutationFn: () => cleanupDependencyReviews(bookId!),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['review-items', bookId] })
      void queryClient.invalidateQueries({ queryKey: ['annotations', bookId] })
      void queryClient.invalidateQueries({ queryKey: ['review-item'] })
      void queryClient.invalidateQueries({ queryKey: ['quote-detail'] })
    },
  })
  const cleanupBlocker = cleanup.isPending ? '正在清理记录，请等待完成。'
    : book.isError ? '书籍读取失败，请先重新读取书籍。'
    : !book.data ? '正在读取书籍，请稍候。'
    : !book.data.active_version_id ? '本书还没有可用正文，请先完成导入。' : null

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  return (
    <div className="ndr-page ndr-review">
      <header className="ndr-reader-header card ndr-page-header">
        <div>
          <h2>待确认队列：{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            这里只列出**已知的待确认项**。队列清空不等于全部识别正确：
            过滤条件与「未知/暂定/过期」数量都要一起看。
          </p>
        </div>
        <nav className="ndr-book-nav" aria-label="本书导航">
          <Link to={`/books/${bookId}/read`}>去阅读</Link>
          <Link to={`/books/${bookId}/preview`}>预览与处理</Link>
          <Link to={`/books/${bookId}/characters`}>全书人物</Link>
          <Link to="/library">返回书架</Link>
        </nav>
      </header>

      <section className="card">
        <button type="button" onClick={() => cleanup.mutate()} disabled={Boolean(cleanupBlocker)}
          title={cleanupBlocker ?? undefined} data-testid="cleanup-dependency-reviews">
          清理历史误触发的重新确认
        </button>
        <p className="hint">清理本书因改单句而连带产生的历史待确认记录，不改变人物归属、不调用模型。真实不确定项、结构调整及来源不明的记录会保留；有任务运行时不能清理。</p>
        {cleanupBlocker && <p className="hint" role="status">{cleanupBlocker}</p>}
        {cleanup.data && <p role="status">已清理 {cleanup.data.resolved_records} 条误触发记录，恢复 {cleanup.data.restored_quotes} 句正常显示；保留 {cleanup.data.preserved_records} 条需继续检查的记录。</p>}
        {cleanup.isError && <p className="status-error" role="alert">清理失败：{cleanup.error.message}</p>}
      </section>

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
                {REASON_LABELS[value]}
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
            共 {counts.targets_total} 个对话：
            {Object.entries(counts.targets_by_status ?? {})
              .map(([key, value]) => `${STATUS_LABELS[key as ReviewQueueStatus] ?? key} ${value}`)
              .join(' · ')}
          </p>
        )}
      </section>

      <section className="card">
        {queue.isPending && <p className="hint">正在读取队列…</p>}
        {queue.isError && <ReadErrorNotice label="队列读取失败" error={queue.error} retrying={queue.isFetching} onRetry={() => void queue.refetch()} />}
        {queue.isSuccess && items.length === 0 && (
          <p className="hint" data-testid="review-empty">
            当前筛选条件下没有待确认项。注意：这**不等于**整本书已完全确认——请同时查看
            「未知/暂定/过期」数量，或调整筛选条件。
          </p>
        )}
        <CollapsibleBlock title="待确认列表" summary={`当前已加载 ${groupedItems.length} 项`}>
        <div data-testid="review-list"><PaginatedItems label="待确认" scope={`${bookId}:${chapterId}:${reason}:${queueStatus}`} listTag="ul" className="ndr-review-list"
          hasMore={queue.hasNextPage} loading={queue.isFetching} loadMore={async () => {
            const result = await queue.fetchNextPage()
            if (result.isError) throw result.error
          }}>
          {groupedItems.map(({ item, reasons, statuses }) => {
            const quoteId = item.quote_id
            const gap = item.gap_id ? gapById.get(item.gap_id) : undefined
            return (
              <li key={item.id} data-testid="review-item" data-review-id={item.id}>
                <div className="ndr-review-item-main">
                  <span className="ndr-badge">{quoteId ? '对白' : '场景边界'}</span>
                  {reasons.map((value) => (
                    <span className={`ndr-badge${value === 'MODEL_OUTPUT_WARNING' ? ' status-warning' : ''}`} key={value}>
                      原因：{REASON_LABELS[value]}
                    </span>
                  ))}
                  {statuses.map((value) => (
                    <span className="ndr-badge" key={value}>
                      {STATUS_LABELS[value]}
                    </span>
                  ))}
                  <span className="ndr-review-text">
                    {item.target_text || gap?.narration || '原文暂不可用'}
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
        </PaginatedItems></div>
        </CollapsibleBlock>
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
