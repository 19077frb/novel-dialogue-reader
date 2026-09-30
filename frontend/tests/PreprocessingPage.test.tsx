import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'

import * as books from '../src/api/books'
import PreprocessingPage from '../src/pages/PreprocessingPage'
import { renderRoute } from './helpers'

vi.mock('../src/api/books', () => ({
  fetchBook: vi.fn(), fetchQuoteNormalizations: vi.fn(), refreshQuoteNormalizations: vi.fn(), updateQuoteNormalization: vi.fn(),
  queryKeys: { book: (id: string) => ['book', id], quoteNormalizations: (id: string) => ['quote-normalizations', id] },
}))
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(books.fetchBook).mockResolvedValue({ title: '测试书' } as never)
  vi.mocked(books.fetchQuoteNormalizations).mockResolvedValue([{ id: 'q1', version: 1, book_version_id: 'v1', opening_cp: 10, close_cp: 17, replacement: '”', source: 'AUTO', status: 'ACTIVE', original_text: '“😀你好。世界', normalized_text: '“😀你好。世界”', reason: '未闭合' }] as never)
})
it('keeps the chapter on return and collapses repair records until requested', async () => {
  renderRoute('/books/:bookId/preprocessing', <PreprocessingPage />, '/books/b1/preprocessing?chapterId=c2')
  expect(await screen.findByRole('heading', { name: '预处理：测试书' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '预览与处理' })).toHaveAttribute('href', '/books/b1/preview?chapterId=c2')
  const record = document.querySelector('.ndr-normalization-card') as HTMLDetailsElement
  expect(record.open).toBe(false)
  await userEvent.click(record.querySelector('summary')!)
  expect(record.open).toBe(true)
  expect(books.refreshQuoteNormalizations).not.toHaveBeenCalled()
})
