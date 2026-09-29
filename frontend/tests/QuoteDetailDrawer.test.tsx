import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import { ApiError } from '../src/api/client'
import * as profilesApi from '../src/api/profiles'
import * as reviewApi from '../src/api/review'
import type { CorrectionOut, QuoteDetailOut, UndoOut } from '../src/api/types'
import { QuoteDetailDrawer } from '../src/components/QuoteDetailDrawer'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/books', () => ({
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
  fetchQuoteDetail: vi.fn(),
  fetchReviewItemDetail: vi.fn(),
  submitQuoteCorrection: vi.fn(),
  submitGapCorrection: vi.fn(),
  undoCorrection: vi.fn(),
  deferReviewItem: vi.fn(),
  flagReviewItem: vi.fn(),
  recheckQuote: vi.fn(),
}))

const DETAIL = {
  quote: {
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
  previous_quote_id: null,
  next_quote_id: null,
  gap_before: {
    gap_id: 'g1',
    book_version_id: 'v1',
    left_quote_id: null,
    right_quote_id: 'q1',
    start_cp: 0,
    end_cp: 10,
    narration: '少女合上伞。',
    paragraph_count: 1,
    decision: 'UNCERTAIN',
  },
  context_before: '少女合上伞。',
  context_after: '少年点头。',
  annotation: {
    quote_id: 'q1',
    scene_id: 's1',
    kind: 'speech',
    assignment: 'EXISTING',
    basis: 'DIRECT',
    speaker_group_id: 'grp1',
    label: 'S1',
    status: 'PROVISIONAL',
    source: 'MODEL',
    stale: false,
    user_locked: false,
    visible_from_cp: 10,
    version: 2,
  },
  scene: { scene_id: 's1', status: 'OPEN', start_cp: 0, end_cp: 30, version: 1 },
  scene_groups: [{ group_id: 'grp1', label: 'S1', canonical_name: '绫濑沙季' }],
  review_items: [],
  can_correct: true,
} as unknown as QuoteDetailOut

const CORRECTION = {
  correction_id: 'corr1',
  correction_ids: ['corr1'],
  action: 'assign_existing',
  target_type: 'quote',
  target_id: 'q1',
  affected_quote_ids: ['q1'],
  stale_quote_ids: ['q2'],
  stale_window_ids: ['hash'],
  created_group_ids: [],
  resolved_review_item_ids: [],
  annotation_versions: { q1: 3 },
  scene_id: 's1',
  scene_version: 1,
  updated_review_counts: { total: 0 },
  undone_by: null,
} as unknown as CorrectionOut

describe('QuoteDetailDrawer', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchQuoteDetail).mockReset()
    vi.mocked(reviewApi.fetchReviewItemDetail).mockReset()
    vi.mocked(reviewApi.submitQuoteCorrection).mockReset()
    vi.mocked(reviewApi.undoCorrection).mockReset()
    vi.mocked(reviewApi.deferReviewItem).mockReset()
    vi.mocked(reviewApi.flagReviewItem).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockReset()

    vi.mocked(booksApi.fetchQuoteDetail).mockResolvedValue(DETAIL)
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
    vi.mocked(reviewApi.submitQuoteCorrection).mockResolvedValue(CORRECTION)
  })

  it('显示原文/上下文/当前标注，展开原文只请求更大上下文', async () => {
    renderWithProviders(
      <QuoteDetailDrawer quoteId="q1" onClose={vi.fn()} />,
    )

    expect(await screen.findByTestId('drawer-quote')).toHaveTextContent('「雨停了。」')
    expect(screen.getByTestId('quote-context')).toHaveTextContent('少女合上伞。')
    expect(screen.getByTestId('quote-context')).toHaveTextContent('少年点头。')
    expect(screen.getByTestId('drawer-annotation')).toHaveTextContent('PROVISIONAL')
    expect(vi.mocked(booksApi.fetchQuoteDetail)).toHaveBeenCalledWith('q1', {
      contextWindowCp: 120,
      signal: expect.anything(),
    })

    await userEvent.click(screen.getByTestId('expand-context'))
    await waitFor(() =>
      expect(vi.mocked(booksApi.fetchQuoteDetail)).toHaveBeenCalledWith('q1', {
        contextWindowCp: 600,
        signal: expect.anything(),
      }),
    )
  })

  it('提交更正时带上期望版本，并报告下游需要重新确认', async () => {
    renderWithProviders(<QuoteDetailDrawer quoteId="q1" onClose={vi.fn()} />)
    await screen.findByTestId('drawer-quote')

    await userEvent.click(screen.getByTestId('correction-submit'))

    await waitFor(() =>
      expect(reviewApi.submitQuoteCorrection).toHaveBeenCalledWith(
        'q1',
        expect.objectContaining({ action: 'assign_existing', expectedVersion: 2 }),
      ),
    )
    expect(await screen.findByTestId('drawer-notice')).toHaveTextContent('1 条下游需要重新确认')
  })

  it('旧版本冲突时提示并刷新，不静默失败', async () => {
    vi.mocked(reviewApi.submitQuoteCorrection).mockRejectedValue(
      new ApiError(409, { code: 'VERSION_CONFLICT', message: '版本冲突' }),
    )
    renderWithProviders(<QuoteDetailDrawer quoteId="q1" onClose={vi.fn()} />)
    await screen.findByTestId('drawer-quote')

    await userEvent.click(screen.getByTestId('correction-submit'))

    expect(await screen.findByTestId('correction-error')).toHaveTextContent('已被其它操作更新')
  })

  it('更正后可以撤销，撤销调用更正记录 ID', async () => {
    vi.mocked(reviewApi.undoCorrection).mockResolvedValue({
      correction_id: 'corr1',
      undo_correction_id: 'undo1',
      target_type: 'quote',
      target_id: 'q1',
      restored: {},
      affected_quote_ids: ['q1'],
      stale_quote_ids: [],
      updated_review_counts: {},
    } as unknown as UndoOut)

    renderWithProviders(<QuoteDetailDrawer quoteId="q1" onClose={vi.fn()} />)
    await screen.findByTestId('drawer-quote')

    const undoButton = screen.getByTestId('drawer-undo')
    expect(undoButton).toBeDisabled()
    await userEvent.click(screen.getByTestId('correction-submit'))
    await waitFor(() => expect(undoButton).toBeEnabled())

    await userEvent.click(undoButton)
    await waitFor(() => expect(reviewApi.undoCorrection).toHaveBeenCalledWith('corr1'))
    expect(await screen.findByTestId('drawer-notice')).toHaveTextContent('已撤销更正')
  })

  it('队列入口才提供「跳过（延后）」', async () => {
    vi.mocked(reviewApi.deferReviewItem).mockResolvedValue({} as never)
    vi.mocked(reviewApi.fetchReviewItemDetail).mockResolvedValue({
      item: {
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
      annotation: DETAIL.annotation,
      scene: DETAIL.scene,
      context_before: '',
      context_after: '',
      allowed_actions: ['assign_existing'],
    } as never)

    renderWithProviders(
      <QuoteDetailDrawer quoteId="q1" reviewItemId="r1" onClose={vi.fn()} />,
    )
    await screen.findByTestId('drawer-queue-item')

    await userEvent.click(screen.getByTestId('drawer-defer'))
    await waitFor(() => expect(reviewApi.deferReviewItem).toHaveBeenCalledWith('r1', '稍后处理'))
  })
})
describe('QuoteDetailDrawer 可访问性', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchQuoteDetail).mockReset()
    vi.mocked(booksApi.fetchQuoteDetail).mockResolvedValue(DETAIL)
    vi.mocked(profilesApi.fetchProfiles).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
    vi.mocked(reviewApi.fetchReviewItemDetail).mockReset()
  })

  it('是带标题的对话框区域，Escape 关闭并把焦点还给打开按钮', async () => {
    const onClose = vi.fn()
    function Host() {
      const [quoteId, setQuoteId] = useState<string | null>(null)
      return (
        <>
          <button type="button" data-testid="drawer-opener" onClick={() => setQuoteId('q1')}>
            打开详情
          </button>
          <QuoteDetailDrawer
            quoteId={quoteId}
            onClose={() => {
              setQuoteId(null)
              onClose()
            }}
          />
        </>
      )
    }

    renderWithProviders(<Host />)
    const opener = screen.getByTestId('drawer-opener')
    opener.focus()
    await userEvent.click(opener)

    const drawer = await screen.findByRole('dialog', { name: '对白确认' })
    const labelledBy = drawer.getAttribute('aria-labelledby')
    expect(labelledBy).toBeTruthy()
    expect(document.getElementById(labelledBy as string)).toHaveTextContent('对白确认')
    await waitFor(() => expect(drawer).toHaveFocus())

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(opener).toHaveFocus())
  })
})
