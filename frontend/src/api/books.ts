/** 书籍/导入/阅读/候选引语相关的查询与写操作封装（TanStack Query 使用）。 */
import { apiData, apiRequest, apiUpload } from './client'
import type {
  BookOut,
  ChapterOut,
  ChapterProcessingCompleteOut,
  ContentResponse,
  CursorPageBook,
  CursorPageGap,
  CursorPageQuote,
  ImportResult,
  JobDetailOut,
  QuoteNormalizationRefreshOut,
  QuoteNormalizationOut,
  QuoteNormalizationUpdateIn,
  QuoteDetailOut,
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
  quotes: (bookId: string, chapterId: string | null) => ['quotes', bookId, chapterId] as const,
  quoteNormalizations: (bookId: string) => ['quote-normalizations', bookId] as const,
  job: (jobId: string) => ['job', jobId] as const,
}

export function fetchBooks(signal?: AbortSignal): Promise<CursorPageBook> {
  return apiData<CursorPageBook>('/api/books', { signal })
}

export function fetchBook(bookId: string, signal?: AbortSignal): Promise<BookOut> {
  return apiData<BookOut>(`/api/books/${bookId}`, { signal })
}

export async function deleteBook(bookId: string): Promise<void> {
  await apiRequest<void>(`/api/books/${bookId}`, { method: 'DELETE' })
}

export function fetchChapters(bookId: string, signal?: AbortSignal): Promise<ChapterOut[]> {
  return apiData<ChapterOut[]>(`/api/books/${bookId}/chapters`, { signal })
}

export function completeChapterProcessing(
  bookId: string,
  chapterId: string,
  bookVersionId: string,
  signal?: AbortSignal,
): Promise<ChapterProcessingCompleteOut> {
  return apiData<ChapterProcessingCompleteOut>(
    `/api/books/${bookId}/chapters/${chapterId}/processing-complete`,
    {
      method: 'POST',
      signal,
      body: { book_version_id: bookVersionId },
    },
  )
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

export interface QuoteQuery {
  chapterId?: string | null
  limit?: number
}

export interface GapQuery {
  limit?: number
  cursor?: string | null
}

/**
 * 候选引语（扫描器结果）。
 * 注意：这只是**候选**，不含任何说话人判断；着色/编号来自 `GET /api/books/{id}/annotations` 的标注投影。
 */
export function fetchQuotes(
  bookId: string,
  query: QuoteQuery = {},
  signal?: AbortSignal,
): Promise<CursorPageQuote> {
  const params = new URLSearchParams()
  if (query.chapterId) params.set('chapter_id', query.chapterId)
  params.set('limit', String(query.limit ?? 500))
  return apiData<CursorPageQuote>(`/api/books/${bookId}/quotes?${params.toString()}`, { signal })
}

export interface QuoteDetailOptions {
  /** 前后各取多少码点的原文（只读本地原文，不调用模型）。 */
  contextWindowCp?: number
  signal?: AbortSignal
}

/** 候选之间的叙述间隔（Gap）；只读，不调用模型。 */
export function fetchGaps(
  bookId: string,
  query: GapQuery = {},
  signal?: AbortSignal,
): Promise<CursorPageGap> {
  const params = new URLSearchParams()
  params.set('limit', String(query.limit ?? 200))
  if (query.cursor) params.set('cursor', query.cursor)
  return apiData<CursorPageGap>(`/api/books/${bookId}/gaps?${params.toString()}`, { signal })
}

export function fetchQuoteDetail(
  quoteId: string,
  options: QuoteDetailOptions = {},
): Promise<QuoteDetailOut> {
  const params = new URLSearchParams()
  if (options.contextWindowCp !== undefined) {
    params.set('context_window_cp', String(options.contextWindowCp))
  }
  const query = params.toString()
  return apiData<QuoteDetailOut>(`/api/quotes/${quoteId}${query ? `?${query}` : ''}`, {
    signal: options.signal,
  })
}

export function fetchQuoteNormalizations(
  bookId: string,
  signal?: AbortSignal,
): Promise<QuoteNormalizationOut[]> {
  return apiData<QuoteNormalizationOut[]>(
    `/api/books/${bookId}/quote-normalizations`,
    { signal },
  )
}

export function refreshQuoteNormalizations(
  bookId: string,
): Promise<QuoteNormalizationRefreshOut> {
  return apiData<QuoteNormalizationRefreshOut>(
    `/api/books/${bookId}/quote-normalizations/refresh`,
    { method: 'POST' },
  )
}

export function updateQuoteNormalization(
  bookId: string,
  normalizationId: string,
  input: QuoteNormalizationUpdateIn,
): Promise<QuoteNormalizationRefreshOut> {
  return apiData<QuoteNormalizationRefreshOut>(
    `/api/books/${bookId}/quote-normalizations/${normalizationId}`,
    { method: 'PUT', body: input },
  )
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

export function fetchJob(jobId: string, signal?: AbortSignal): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/jobs/${jobId}`, { signal })
}

/** 受控资源地址（图片等）；只读取后端已登记的包内资源。 */
export function resourceUrl(bookId: string, resourceId: string): string {
  return `/api/books/${bookId}/resources/${resourceId}`
}
