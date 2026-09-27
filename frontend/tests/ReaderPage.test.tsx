import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import type { BookOut, ChapterOut, ContentNodeOut } from '../src/api/types'
import ReaderPage, { findCurrentStartCp } from '../src/pages/ReaderPage'
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
  },
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchContent: vi.fn(),
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
      text: chapterId === 'c2' ? '第二章的正文。' : '第一章的正文。',
      payload: {},
    },
  ] as ContentNodeOut[]
}

describe('ReaderPage', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchContent).mockReset()
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

    expect(await screen.findByText('第二章的正文。')).toBeInTheDocument()
    const active = screen.getByRole('button', { current: true })
    expect(active).toHaveTextContent('第二章')
  })

  it('切换章节时保存阅读位置', async () => {
    renderRoute('/books/:bookId/read', <ReaderPage />, '/books/b1/read')
    await screen.findByText('第二章的正文。')

    await userEvent.click(screen.getByRole('button', { name: /第一章/ }))

    await waitFor(() => expect(booksApi.saveReadingProgress).toHaveBeenCalledTimes(1))
    expect(booksApi.saveReadingProgress).toHaveBeenCalledWith('b1', {
      bookVersionId: 'v1',
      readPositionCp: 0,
      readingMode: 'initial',
      expectedVersion: 3,
    })
    expect(await screen.findByText('第一章的正文。')).toBeInTheDocument()
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
  })
})