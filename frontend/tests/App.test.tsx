import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as books from '../src/api/books'
import * as client from '../src/api/client'
import App from '../src/App'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/client', () => ({
  fetchHealth: vi.fn(),
  ApiError: class ApiError extends Error {},
}))
vi.mock('../src/api/books', () => ({
  queryKeys: {
    health: () => ['health'],
    books: () => ['books'],
    book: (id: string) => ['book', id],
    chapters: (id: string) => ['chapters', id],
    content: (id: string, chapter: string | null, cursor: string | null) => [
      'content',
      id,
      chapter,
      cursor,
    ],
    job: (id: string) => ['job', id],
  },
  fetchBooks: vi.fn(),
  importBook: vi.fn(),
  fetchJob: vi.fn(),
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchContent: vi.fn(),
  saveReadingProgress: vi.fn(),
  resourceUrl: (bookId: string, resourceId: string) =>
    `/api/books/${bookId}/resources/${resourceId}`,
}))

const mockedHealth = vi.mocked(client.fetchHealth)
const mockedBooks = vi.mocked(books.fetchBooks)

describe('App', () => {
  beforeEach(() => {
    mockedHealth.mockReset()
    mockedBooks.mockReset()
  })

  it('显示后端返回的健康状态并默认进入书架', async () => {
    mockedHealth.mockResolvedValue({
      status: 'ok',
      app: 'novel-dialogue-reader',
      version: '9.9.9-test',
      api_version: '1',
      environment: 'test',
      started_at: '2026-09-28T00:00:00+00:00',
      uptime_seconds: 12.5,
      server_time: '2026-09-28T00:00:12.500000+00:00',
      database: { state: 'READY', revision: '0004', head_revision: '0004' },
    })
    mockedBooks.mockResolvedValue({ items: [], next_cursor: null })

    renderWithProviders(<App />, '/')

    expect(await screen.findByTestId('health-ok')).toHaveTextContent('数据库 就绪')
    expect(screen.getByTestId('health-ok')).toHaveTextContent('9.9.9-test')
    expect(await screen.findByTestId('library-empty')).toBeInTheDocument()
  })

  it('后端不可用时给出可理解的错误与重试入口', async () => {
    mockedHealth.mockRejectedValue(new Error('连接被拒绝'))
    mockedBooks.mockResolvedValue({ items: [], next_cursor: null })

    renderWithProviders(<App />, '/')

    expect(await screen.findByTestId('health-error')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试连接' })).toBeInTheDocument()
  })
})
