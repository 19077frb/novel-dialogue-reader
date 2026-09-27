import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as books from '../src/api/books'
import { ApiError } from '../src/api/client'
import type { ImportResult } from '../src/api/types'
import LibraryPage from '../src/pages/LibraryPage'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/books', () => ({
  queryKeys: {
    books: () => ['books'],
    job: (id: string) => ['job', id],
  },
  fetchBooks: vi.fn(),
  importBook: vi.fn(),
  fetchJob: vi.fn(),
}))

const mockedFetchBooks = vi.mocked(books.fetchBooks)
const mockedImport = vi.mocked(books.importBook)
const mockedFetchJob = vi.mocked(books.fetchJob)

const IMPORT_RESULT: ImportResult = {
  book_id: 'b1',
  book_version_id: 'v1',
  job_id: 'j1',
  format: 'TXT',
  import_status: 'COMPLETED',
  encoding: 'gb18030',
  encoding_confidence: 'medium',
  chapter_count: 3,
  node_count: 12,
  resource_count: 0,
  canonical_length_cp: 143,
  reused_book: false,
  reused_version: false,
  warnings: ['原文行尾不是 LF，导入时已规范化为 LF（source_map 标记为 synthetic）。'],
}

async function pickFile() {
  const input = screen.getByTestId('import-file-input') as HTMLInputElement
  const file = new File(['第一章\n正文。\n'], '雨夜.txt', { type: 'text/plain' })
  await userEvent.upload(input, file)
  return file
}

describe('LibraryPage', () => {
  beforeEach(() => {
    mockedFetchBooks.mockReset()
    mockedImport.mockReset()
    mockedFetchJob.mockReset()
    mockedFetchBooks.mockResolvedValue({ items: [], next_cursor: null })
    mockedFetchJob.mockResolvedValue({
      id: 'j1',
      kind: 'IMPORT',
      purpose: null,
      state: 'COMPLETED',
      book_id: 'b1',
      book_version_id: 'v1',
      progress: { chapters: 3, nodes: 12 },
      checkpoint: null,
      last_error: null,
      created_at: '2026-09-28T00:00:00+00:00',
      updated_at: '2026-09-28T00:00:01+00:00',
    })
  })

  it('空书架给出无需 API 配置即可阅读的提示', async () => {
    renderWithProviders(<LibraryPage />)
    expect(await screen.findByTestId('library-empty')).toHaveTextContent('不需要填写任何 API 配置')
  })

  it('导入成功后显示章节/节点数量与警告', async () => {
    mockedImport.mockResolvedValue(IMPORT_RESULT)
    renderWithProviders(<LibraryPage />)

    await pickFile()
    await userEvent.click(screen.getByTestId('import-submit'))

    const result = await screen.findByTestId('import-result')
    expect(result).toHaveTextContent('导入完成：TXT，3 章，12 个节点，143 码点')
    expect(result).toHaveTextContent('编码 gb18030')
    expect(result).toHaveTextContent('synthetic')
    await waitFor(() => expect(mockedImport).toHaveBeenCalledTimes(1))
  })

  it('编码选错时给出候选与有损预演，并可一键换编码重试', async () => {
    mockedImport.mockRejectedValueOnce(
      new ApiError(422, {
        code: 'VALIDATION_ERROR',
        message: '按 utf-8 解码失败',
        details: {
          requested_encoding: 'utf-8',
          preview: '���� ��ҹ',
          preview_is_lossy: true,
          candidates: [
            { encoding: 'utf-8', ok: false, plausibility: 0 },
            { encoding: 'gb18030', ok: true, plausibility: 1 },
            { encoding: 'big5', ok: false, plausibility: 0 },
          ],
        },
      }),
    )
    mockedImport.mockResolvedValueOnce(IMPORT_RESULT)

    renderWithProviders(<LibraryPage />)
    await pickFile()
    await userEvent.click(screen.getByTestId('import-submit'))

    const error = await screen.findByTestId('import-error')
    expect(error).toHaveTextContent('导入失败（VALIDATION_ERROR）')
    expect(screen.getByTestId('import-preview')).toHaveTextContent('���� ��ҹ')
    expect(screen.queryByTestId('retry-utf-8')).toBeNull()
    expect(screen.getByTestId('retry-gb18030')).toBeInTheDocument()

    await userEvent.click(screen.getByTestId('retry-gb18030'))
    await waitFor(() => expect(mockedImport).toHaveBeenCalledTimes(2))
    expect(mockedImport.mock.calls[1][0].encoding).toBe('gb18030')
    expect(await screen.findByTestId('import-result')).toBeInTheDocument()
  })

  it('格式不支持时给出可理解的错误', async () => {
    mockedImport.mockRejectedValueOnce(
      new ApiError(415, {
        code: 'UNSUPPORTED_MEDIA_TYPE',
        message: '只支持 .txt 与 .epub 文件',
        details: { filename: 'book.md' },
      }),
    )
    renderWithProviders(<LibraryPage />)
    await pickFile()
    await userEvent.click(screen.getByTestId('import-submit'))

    const error = await screen.findByTestId('import-error')
    expect(error).toHaveTextContent('只支持 .txt 与 .epub 文件')
  })
})