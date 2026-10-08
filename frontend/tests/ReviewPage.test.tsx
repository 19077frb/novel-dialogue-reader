import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import * as profilesApi from '../src/api/profiles'
import * as reviewApi from '../src/api/review'
import type { ReviewQueueResponse } from '../src/api/types'
import ReviewPage from '../src/pages/ReviewPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/books', () => ({
  queryKeys: {
    book: (id: string) => ['book', id],
    chapters: (id: string) => ['chapters', id],
    content: (id: string, chapter: string | null, cursor: string | null) => [
      'content',
      id,
      chapter,
      cursor,
    ],
    quotes: (id: string, chapter: string | null) => ['quotes', id, chapter],
    job: (id: string) => ['job', id],
  },
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchQuotes: vi.fn(),
  fetchGaps: vi.fn(),
  fetchQuoteDetail: vi.fn(),
}))

vi.mock('../src/api/profiles', () => ({
  profileKeys: { profiles: () => ['model-profiles'], protocols: () => ['protocols'] },
  fetchProfiles: vi.fn(),
}))

vi.mock('../src/api/review', () => ({
  reviewKeys: {
    queue: (bookId: string, filters: unknown) => ['review-items', bookId, filters],
    detail: (itemId: string) => ['review-item', itemId],
    quoteDetail: (quoteId: string, contextWindowCp: number) => [
      'quote-detail',
      quoteId,
      contextWindowCp,
    ],
  },
  fetchReviewQueue: vi.fn(),
  cleanupDependencyReviews: vi.fn(),
  fetchReviewItemDetail: vi.fn(),
  submitGapCorrection: vi.fn(),
  submitQuoteCorrection: vi.fn(),
  undoCorrection: vi.fn(),
  deferReviewItem: vi.fn(),
  flagReviewItem: vi.fn(),
  recheckQuote: vi.fn(),
}))

const BOOK = {
  id: 'b1',
  title: '雨夜',
  format: 'TXT',
  source_sha256: 'a'.repeat(64),
  import_status: 'COMPLETED',
  read_position_cp: 0,
  reading_mode: 'initial',
  version: 1,
  active_version_id: 'v1',
  active_version: {
    id: 'v1',
    encoding: 'utf-8',
    parser_version: 'txt-1',
    normalization_version: 'canonical-lf-1',
    canonical_sha256: 'b'.repeat(64),
    canonical_length_cp: 60,
    warnings: [],
    created_at: '2026-09-28T00:00:00+00:00',
  },
  created_at: '2026-09-28T00:00:00+00:00',
  updated_at: '2026-09-28T00:00:00+00:00',
} as never

const QUEUE = {
  items: [
    {
      id: 'r1',
      target_type: 'quote',
      quote_id: 'q1',
      gap_id: null,
      target_text: '「雨停了。」',
      reason: 'LOW_CONFIDENCE',
      queue_status: 'PENDING',
      candidates: {},
      annotation_version: 2,
      resolved_by_correction_id: null,
      version: 1,
      created_at: '2026-09-28T00:00:00+00:00',
      updated_at: '2026-09-28T00:00:00+00:00',
    },
    {
      id: 'r1b',
      target_type: 'quote',
      quote_id: 'q1',
      gap_id: null,
      target_text: '「雨停了。」',
      reason: 'AMBIGUOUS_SPEAKER',
      queue_status: 'PENDING',
      candidates: {},
      annotation_version: 2,
      resolved_by_correction_id: null,
      version: 1,
      created_at: '2026-09-28T00:00:00+00:00',
      updated_at: '2026-09-28T00:00:00+00:00',
    },
    {
      id: 'r2',
      target_type: 'gap',
      quote_id: null,
      gap_id: 'gap1',
      target_text: '少女合上伞。',
      reason: 'SCENE_BOUNDARY',
      queue_status: 'PENDING',
      candidates: {},
      annotation_version: null,
      resolved_by_correction_id: null,
      version: 1,
      created_at: '2026-09-28T00:00:01+00:00',
      updated_at: '2026-09-28T00:00:01+00:00',
    },
  ],
  next_cursor: null,
  counts: {
    total: 3,
    by_status: { PENDING: 3 },
    by_reason: { LOW_CONFIDENCE: 1, AMBIGUOUS_SPEAKER: 1, SCENE_BOUNDARY: 1 },
    targets_total: 2,
    targets_by_status: { PENDING: 2 },
  },
} as never

describe('ReviewPage', () => {
  it('待确认跨页去重并按需读取后续批次', async () => {
    const source = QUEUE as ReviewQueueResponse
    const rows = Array.from({ length: 21 }, (_, i) => ({ ...source.items![0], id: `r${i}`, quote_id: `q${i}`, target_text: `「测试对白 ${i}」` }))
    vi.mocked(reviewApi.fetchReviewQueue).mockImplementation(async (_book, filters) => ({
      ...source, items: filters?.cursor ? [rows[20], { ...rows[20], id: 'other-reason', reason: 'UNKNOWN_SPEAKER' }] : rows,
      next_cursor: filters?.cursor ? null : 'next',
    }) as never)
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    await screen.findByText('「测试对白 0」')
    const nav = screen.getByRole('navigation', { name: '待确认分页' })
    expect(screen.getByText('「测试对白 20」')).not.toBeVisible()
    await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
    expect(reviewApi.fetchReviewQueue).toHaveBeenCalledTimes(1)
    await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
    await waitFor(() => expect(screen.getByText('原因：无法确定说话人')).toBeVisible())
    expect(screen.getAllByText('「测试对白 20」')).toHaveLength(1)
    expect(reviewApi.fetchReviewQueue).toHaveBeenCalledWith('b1', expect.objectContaining({ cursor: 'next' }), expect.anything())
  })
  beforeEach(() => {
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchQuotes).mockReset()
    vi.mocked(booksApi.fetchGaps).mockReset()
    vi.mocked(booksApi.fetchQuoteDetail).mockReset()
    vi.mocked(reviewApi.fetchReviewQueue).mockReset()
    vi.mocked(reviewApi.submitGapCorrection).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockReset()

    vi.mocked(booksApi.fetchBook).mockResolvedValue(BOOK)
    vi.mocked(booksApi.fetchChapters).mockResolvedValue([
      { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 60, source_href: null },
    ] as never)
    vi.mocked(booksApi.fetchQuotes).mockResolvedValue({
      items: [
        {
          quote_id: 'q1',
          book_version_id: 'v1',
          chapter_id: 'c1',
          chapter_ordinal: 0,
          start_cp: 10,
          end_cp: 16,
          text: '雨停了。',
          delimited_text: '「雨停了。」',
          delimiter: 'corner_bracket',
          opening: '「',
          closing: '」',
          nesting_depth: 0,
          parent_quote_id: null,
          kind_hint: null,
          scanner_version: 'quote-scan-1',
        normalized: false,
        },
      ],
      next_cursor: null,
    } as never)
    vi.mocked(booksApi.fetchGaps).mockResolvedValue({
      items: [
        {
          gap_id: 'gap1',
          book_version_id: 'v1',
          left_quote_id: null,
          right_quote_id: 'q1',
          start_cp: 0,
          end_cp: 10,
          narration: '少女合上伞。',
          paragraph_count: 1,
          decision: 'UNCERTAIN',
        },
      ],
      next_cursor: null,
    } as never)
    vi.mocked(reviewApi.fetchReviewQueue).mockResolvedValue(QUEUE)
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
  })

  it('列出队列项（带对白原文与 Gap 叙述）并显示权威计数', async () => {
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')

    const rows = await screen.findAllByTestId('review-item')
    expect(rows).toHaveLength(2)
    expect(rows[0]).toHaveTextContent('「雨停了。」')
    expect(rows[0]).toHaveTextContent('原因：置信度低')
    expect(rows[0]).toHaveTextContent('原因：说话人有歧义')
    expect(screen.getAllByText('「雨停了。」')).toHaveLength(1)
    expect(rows[1]).toHaveTextContent('少女合上伞。')
    expect(rows[1]).toHaveTextContent('原因：场景边界待确认')
    expect(screen.getByTestId('review-counts')).toHaveTextContent('共 2 个对话')
    expect(screen.getByTestId('review-counts')).toHaveTextContent('待确认 2')
  })

  it('空队列明确说明「不等于全部识别正确」', async () => {
    vi.mocked(reviewApi.fetchReviewQueue).mockResolvedValue({
      items: [],
      next_cursor: null,
      counts: {
        total: 0,
        by_status: {},
        by_reason: {},
        targets_total: 0,
        targets_by_status: {},
      },
    } as never)

    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    expect(await screen.findByTestId('review-empty')).toHaveTextContent('不等于')
  })

  it('校验警告有中文原因和警告样式，并可按原因筛选', async () => {
    const source = QUEUE as ReviewQueueResponse
    vi.mocked(reviewApi.fetchReviewQueue).mockResolvedValue({
      ...source,
      items: [{ ...source.items![0], reason: 'MODEL_OUTPUT_WARNING' }],
    } as never)
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    expect(await screen.findByText('原因：模型结果校验警告')).toHaveClass('status-warning')
    expect(screen.getByRole('option', { name: '模型结果校验警告' })).toBeInTheDocument()
  })

  it('清理误触发记录后重新读取队列，保留真实问题并显示统计', async () => {
    vi.mocked(reviewApi.cleanupDependencyReviews).mockResolvedValue({
      resolved_records: 100, restored_quotes: 100, preserved_records: 2,
    })
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    const beforeRows = await screen.findAllByTestId('review-item')
    await userEvent.click(screen.getByTestId('cleanup-dependency-reviews'))
    expect(reviewApi.cleanupDependencyReviews).toHaveBeenCalledWith('b1')
    expect(await screen.findByText(/已清理 100 条误触发记录/)).toHaveTextContent('保留 2 条')
    await waitFor(() => expect(reviewApi.fetchReviewQueue).toHaveBeenCalledTimes(2))
    expect(screen.getAllByTestId('review-item')).toHaveLength(beforeRows.length)
  })

  it('清理被运行任务阻止时显示真实原因', async () => {
    vi.mocked(reviewApi.cleanupDependencyReviews).mockRejectedValue(new Error('请等待本书任务结束'))
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    await screen.findAllByTestId('review-item')
    await userEvent.click(screen.getByTestId('cleanup-dependency-reviews'))
    expect(await screen.findByRole('alert')).toHaveTextContent('请等待本书任务结束')
  })

  it('Gap 项走 Gap 更正接口（不能误用说话人确认）', async () => {
    vi.mocked(reviewApi.submitGapCorrection).mockResolvedValue({
      correction_id: 'c1',
      gap_id: 'gap1',
      decision: 'BREAK',
      previous_decision: 'UNCERTAIN',
      affected_quote_ids: ['q1'],
      stale_quote_ids: [],
      closed_scene_ids: ['s1'],
      opened_scene_id: 's2',
      resolved_review_item_ids: [],
      created_review_item_ids: [],
      updated_review_counts: {},
    } as never)

    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    const rows = await screen.findAllByTestId('review-item')
    await userEvent.click(within(rows[1]).getByTestId('review-open-gap'))
    await userEvent.click(within(rows[1]).getByTestId('gap-decision-BREAK'))

    await waitFor(() =>
      expect(reviewApi.submitGapCorrection).toHaveBeenCalledWith('gap1', {
        decision: 'BREAK',
      }),
    )
  })

  it('筛选条件会带进队列查询（按章节）', async () => {
    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    await screen.findAllByTestId('review-item')

    await userEvent.selectOptions(screen.getByTestId('review-chapter'), 'c1')
    await waitFor(() =>
      expect(vi.mocked(reviewApi.fetchReviewQueue)).toHaveBeenCalledWith(
        'b1',
        expect.objectContaining({ chapterId: 'c1' }),
        expect.anything(),
      ),
    )
  })
})
