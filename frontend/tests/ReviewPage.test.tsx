import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import * as profilesApi from '../src/api/profiles'
import * as reviewApi from '../src/api/review'
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
      id: 'r2',
      target_type: 'gap',
      quote_id: null,
      gap_id: 'gap1',
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
    total: 2,
    by_status: { PENDING: 2 },
    by_reason: { LOW_CONFIDENCE: 1, SCENE_BOUNDARY: 1 },
  },
} as never

describe('ReviewPage', () => {
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
    expect(rows[0]).toHaveTextContent('LOW_CONFIDENCE')
    expect(rows[0]).toHaveTextContent('「雨停了。」')
    expect(rows[1]).toHaveTextContent('少女合上伞。')
    expect(screen.getByTestId('review-counts')).toHaveTextContent('共 2 项')
    expect(screen.getByTestId('review-counts')).toHaveTextContent('PENDING 2')
  })

  it('空队列明确说明「不等于全部识别正确」', async () => {
    vi.mocked(reviewApi.fetchReviewQueue).mockResolvedValue({
      items: [],
      next_cursor: null,
      counts: { total: 0, by_status: {}, by_reason: {} },
    } as never)

    renderRoute('/books/:bookId/review', <ReviewPage />, '/books/b1/review')
    expect(await screen.findByTestId('review-empty')).toHaveTextContent('不等于')
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