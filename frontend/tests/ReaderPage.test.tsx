import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as annotationsApi from '../src/api/annotations'
import * as booksApi from '../src/api/books'
import type { BookOut, ChapterOut, ContentNodeOut } from '../src/api/types'
import ReaderPage, { findCurrentStartCp } from '../src/pages/ReaderPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/annotations', () => ({
  annotationKeys: {
    range: (
      bookId: string,
      startCp: number,
      endCp: number,
      mode: string,
      horizon: number | null,
    ) => ['annotations', bookId, startCp, endCp, mode, horizon],
  },
  fetchAnnotations: vi.fn(),
}))

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
  },
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchContent: vi.fn(),
  fetchQuotes: vi.fn(),
  saveReadingProgress: vi.fn(),
  resourceUrl: (bookId: string, resourceId: string) =>
    `/api/books/${bookId}/resources/${resourceId}`,
}))

const BOOK: BookOut = {
  id: 'b1',
  title: '雨夜',
  format: 'TXT',
  source_sha256: 'a'.repeat(64),
  import_status: 'COMPLETED',
  read_position_cp: 30,
  reading_mode: 'initial',
  version: 3,
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
} as BookOut

const CHAPTERS: ChapterOut[] = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 20, source_href: null },
  { id: 'c2', ordinal: 1, title: '第二章', start_cp: 21, end_cp: 60, source_href: null },
] as ChapterOut[]

function nodesFor(chapterId: string): ContentNodeOut[] {
  return [
    {
      node_id: 'n00000',
      node_type: 'paragraph',
      ordinal: 0,
      start_cp: chapterId === 'c2' ? 21 : 0,
      end_cp: chapterId === 'c2' ? 30 : 9,
      chapter_id: chapterId,
      chapter_ordinal: chapterId === 'c2' ? 1 : 0,
      text: chapterId === 'c2' ? '「第二章的正文。」' : '「第一章的正文。」',
      payload: {},
    },
  ] as ContentNodeOut[]
}

function quotesFor(chapterId: string) {
  const start = chapterId === 'c2' ? 21 : 0
  return {
    items: [
      {
        quote_id: `q-${chapterId}`,
        book_version_id: 'v1',
        chapter_id: chapterId,
        chapter_ordinal: chapterId === 'c2' ? 1 : 0,
        start_cp: start,
        end_cp: start + 9,
        text: chapterId === 'c2' ? '第二章的正文。' : '第一章的正文。',
        delimited_text: chapterId === 'c2' ? '「第二章的正文。」' : '「第一章的正文。」',
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
  }
}

describe('ReaderPage', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchContent).mockReset()
    vi.mocked(booksApi.fetchQuotes).mockReset()
    vi.mocked(annotationsApi.fetchAnnotations).mockReset()
    vi.mocked(booksApi.saveReadingProgress).mockReset()

    vi.mocked(booksApi.fetchBook).mockResolvedValue(BOOK)
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(CHAPTERS)
    vi.mocked(booksApi.fetchContent).mockImplementation(async (_bookId, query) => ({
      book_id: 'b1',
      book_version_id: 'v1',
      canonical_length_cp: 60,
      chapter_id: query?.chapterId ?? null,
      start_cp: 0,
      end_cp: 60,
      nodes: nodesFor(query?.chapterId ?? 'c1'),
      next_cursor: null,
    }))
    vi.mocked(booksApi.fetchQuotes).mockImplementation(async (_bookId, query) =>
      quotesFor(query?.chapterId ?? 'c1'),
    )
    vi.mocked(annotationsApi.fetchAnnotations).mockResolvedValue({
      identity_reverts: 0,
      book_id: 'b1',
      book_version_id: 'v1',
      reading_mode: 'initial',
      visible_horizon_cp: 30,
      start_cp: 21,
      end_cp: 60,
      items: [
        {
          quote_id: 'q-c2',
          scene_id: 's1',
          start_cp: 21,
          end_cp: 30,
          kind: 'speech',
          assignment: 'EXISTING',
          basis: 'DIRECT',
          status: 'ACCEPTED',
          source: 'MODEL',
          speaker_group_id: 'g1',
          label: 'S1',
          speaker_description: '本章主人公',
          color_index: 0,
          stale: false,
          user_locked: false,
          withheld: false,
        },
      ],
      legend: [
        {
          group_id: 'g1',
          label: 'S1',
          scene_id: 's1',
          color_index: 0,
          first_quote_id: 'q-c2',
          description: '',
          quote_count: 1,
        },
      ],
      counts: {
        total: 1,
        accepted: 1,
        provisional: 0,
        unknown: 0,
        stale: 0,
        withheld: 0,
        unprocessed_quotes: 0,
      },
      scenes: [],
    })
    vi.mocked(booksApi.saveReadingProgress).mockResolvedValue({
      book_id: 'b1',
      book_version_id: 'v1',
      read_position_cp: 21,
      reading_mode: 'initial',
      version: 4,
    })
  })

  it('按书签位置打开对应章节并渲染正文', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')

    expect(await screen.findByText(/第二章的正文/)).toBeInTheDocument()
    const active = screen.getByRole('button', { current: true })
    expect(active).toHaveTextContent('第二章')
    expect(screen.getByRole('link', { name: '预览与处理' })).toHaveAttribute('href', '/books/b1/preview?chapterId=c2')
  })

  it('删除前文按钮，书签跳转保留章首并自动加载到目标分页', async () => {
    vi.mocked(booksApi.fetchContent).mockImplementation(async (_bookId, query) => ({
      book_id: 'b1', book_version_id: 'v1', canonical_length_cp: 60,
      chapter_id: query?.chapterId ?? null, start_cp: 21, end_cp: 60,
      nodes: query?.cursor
        ? [{ ...nodesFor('c2')[0], node_id: 'later', start_cp: 40, end_cp: 45, text: '目标句子。' }]
        : nodesFor('c2'),
      next_cursor: query?.cursor ? null : 'second-page',
    }))
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read?chapterId=c2&positionCp=40')
    expect(await screen.findByText(/目标句子/)).toBeInTheDocument()
    expect(screen.getByText(/第二章的正文/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '查看本章前文' })).not.toBeInTheDocument()
    expect(vi.mocked(booksApi.fetchContent).mock.calls).toHaveLength(2)
    for (const [, query] of vi.mocked(booksApi.fetchContent).mock.calls) expect(query?.startCp).toBeUndefined()
  })

  it('书籍读取失败时显示具体错误并允许重新读取', async () => {
    vi.mocked(booksApi.fetchBook).mockRejectedValueOnce(new Error('数据库暂时繁忙'))
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')

    expect(await screen.findByTestId('book-read-error')).toHaveTextContent(
      '书籍读取失败：数据库暂时繁忙',
    )
    vi.mocked(booksApi.fetchBook).mockResolvedValueOnce(BOOK)
    await userEvent.click(screen.getByRole('button', { name: '重新读取' }))

    expect(await screen.findByText(/第二章的正文/)).toBeInTheDocument()
  })

  it('返回链接的章节优先于旧书签，后续手动换章不会被链接覆盖', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read?chapterId=c1')
    expect(await screen.findByText(/第一章的正文/)).toBeInTheDocument()
    expect(screen.getByRole('button', { current: true })).toHaveTextContent('第一章')
    await userEvent.click(screen.getByRole('button', { name: /第二章/ }))
    expect(await screen.findByText(/第二章的正文/)).toBeInTheDocument()
    expect(screen.getByRole('button', { current: true })).toHaveTextContent('第二章')
  })

  it('无效或外书章节链接回退到本书书签章节', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read?chapterId=foreign-chapter')
    expect(await screen.findByText(/第二章的正文/)).toBeInTheDocument()
  })

  it('切换章节时保存阅读位置', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')
    await screen.findByText(/第二章的正文/)

    await userEvent.click(screen.getByRole('button', { name: /第一章/ }))

    await waitFor(() => expect(booksApi.saveReadingProgress).toHaveBeenCalledTimes(1))
    expect(booksApi.saveReadingProgress).toHaveBeenCalledWith('b1', {
      bookVersionId: 'v1',
      readPositionCp: 0,
      readingMode: 'initial',
      expectedVersion: 3,
    })
    expect(await screen.findByText(/第一章的正文/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '预览与处理' })).toHaveAttribute('href', '/books/b1/preview?chapterId=c1')
  })

  it('显示候选引语覆盖，并可关闭（覆盖不等于识别结果）', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')

    const marks = await screen.findAllByTestId('candidate-quote')
    expect(marks).toHaveLength(1)
    expect(marks[0]).toHaveTextContent('「第二章的正文。」')
    expect(screen.getByText(/候选引语 1 条/)).toBeInTheDocument()

    await userEvent.click(screen.getByTestId('toggle-candidates'))
    await waitFor(() => expect(screen.queryAllByTestId('candidate-quote')).toHaveLength(0))
  })

  it('显示后端投影的颜色/编号，并可关闭标注（原文不变）', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')
    await screen.findByText(/第二章的正文/)

    const span = screen.getByTestId('annotation-span')
    expect(span).toHaveTextContent('「第二章的正文。」')
    expect(screen.getAllByTestId('annotation-label')[0]).toHaveTextContent('〔S1〕')
    expect(screen.getByTestId('speaker-legend')).toHaveTextContent('S1')
    expect(screen.getByText(/标注 1 条/)).toBeInTheDocument()

    const callsBefore = vi.mocked(annotationsApi.fetchAnnotations).mock.calls.length
    await userEvent.click(screen.getByTestId('toggle-annotations'))
    await waitFor(() => expect(screen.queryByTestId('annotation-span')).toBeNull())
    expect(screen.getByText(/第二章的正文/)).toBeInTheDocument()
    // 只改显示：不会重新拉取投影，也不会有任何模型调用
    expect(vi.mocked(annotationsApi.fetchAnnotations).mock.calls.length).toBe(callsBefore)
  })

  it('切到重读模式会按新阅读模式重新取投影', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')
    await screen.findByText(/第二章的正文/)

    await userEvent.selectOptions(screen.getByTestId('reader-reading-mode'), 'reread')
    await waitFor(() =>
      expect(
        vi.mocked(annotationsApi.fetchAnnotations).mock.calls.some(
          (call) => (call[1] as { readingMode?: string } | undefined)?.readingMode === 'reread',
        ),
      ).toBe(true),
    )
  })

  it('findCurrentStartCp 取第一个进入视口的节点', () => {
    const make = (top: number, startCp: number) =>
      ({
        getBoundingClientRect: () => ({ top }),
        dataset: { startCp: String(startCp) },
      }) as unknown as HTMLElement

    expect(findCurrentStartCp([])).toBeNull()
    expect(findCurrentStartCp([make(-50, 5), make(10, 20)])).toBe(20)
    expect(findCurrentStartCp([make(-50, 5), make(-10, 9)])).toBe(9)
    const partiallyVisible = { getBoundingClientRect: () => ({ top: -50, bottom: 30 }), dataset: { startCp: '5' } } as unknown as HTMLElement
    expect(findCurrentStartCp([partiallyVisible, make(40, 20)])).toBe(5)
  })
})
