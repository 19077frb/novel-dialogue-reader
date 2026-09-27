/** 书籍/导入/阅读相关的查询与写操作封装（TanStack Query 使用）。 */
import { apiData, apiUpload } from './client'
import type {
  BookOut,
  ChapterOut,
  ContentResponse,
  CursorPageBook,
  ImportResult,
  JobOut,
  ReadingMode,
  ReadingProgressOut,
} from './types'

export const queryKeys = {
  health: () => ['health'] as const,
  books: () => ['books'] as const,
  book: (bookId: string) => ['book', bookId] as const,
  chapters: (bookId: string) => ['chapters', bookId] as const,
  content: (bookId: string, chapterId: string | null, cursor: string | null) =>
    ['content', bookId, chapterId, cursor] as const,
  job: (jobId: string) => ['job', jobId] as const,
}

export function fetchBooks(signal?: AbortSignal): Promise<CursorPageBook> {
  return apiData<CursorPageBook>('/api/books', { signal })
}

export function fetchBook(bookId: string, signal?: AbortSignal): Promise<BookOut> {
  return apiData<BookOut>(`/api/books/${bookId}`, { signal })
}

export function fetchChapters(bookId: string, signal?: AbortSignal): Promise<ChapterOut[]> {
  return apiData<ChapterOut[]>(`/api/books/${bookId}/chapters`, { signal })
}

export interface ContentQuery {
  chapterId?: string | null
  cursor?: string | null
  limit?: number
}

export function fetchContent(
  bookId: string,
  query: ContentQuery = {},
  signal?: AbortSignal,
): Promise<ContentResponse> {
  const params = new URLSearchParams()
  if (query.chapterId) params.set('chapter_id', query.chapterId)
  if (query.cursor) params.set('cursor', query.cursor)
  params.set('limit', String(query.limit ?? 500))
  return apiData<ContentResponse>(`/api/books/${bookId}/content?${params.toString()}`, { signal })
}

export interface ImportInput {
  file: File
  encoding?: string
  title?: string
}

export function importBook(input: ImportInput, signal?: AbortSignal): Promise<ImportResult> {
  const form = new FormData()
  form.append('file', input.file)
  if (input.encoding) form.append('encoding', input.encoding)
  if (input.title) form.append('title', input.title)
  return apiUpload<ImportResult>('/api/books/import', form, { signal })
}

export interface ProgressInput {
  bookVersionId: string
  readPositionCp: number
  readingMode: ReadingMode
  expectedVersion?: number
}

export function saveReadingProgress(
  bookId: string,
  input: ProgressInput,
  signal?: AbortSignal,
): Promise<ReadingProgressOut> {
  return apiData<ReadingProgressOut>(`/api/books/${bookId}/reading-progress`, {
    method: 'PUT',
    signal,
    body: {
      book_version_id: input.bookVersionId,
      read_position_cp: input.readPositionCp,
      reading_mode: input.readingMode,
      expected_version: input.expectedVersion ?? null,
    },
  })
}

export function fetchJob(jobId: string, signal?: AbortSignal): Promise<JobOut> {
  return apiData<JobOut>(`/api/jobs/${jobId}`, { signal })
}

/** 受控资源地址（图片等）；只读取后端已登记的包内资源。 */
export function resourceUrl(bookId: string, resourceId: string): string {
  return `/api/books/${bookId}/resources/${resourceId}`
}