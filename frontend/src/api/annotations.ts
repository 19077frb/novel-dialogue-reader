/** 标注投影查询（T11）。只读：拉取范围、版本、阅读模式与 horizon 下的有效颜色/编号。 */
import { apiData } from './client'
import type { AnnotationsResponse, ReadingMode } from './types'

export const annotationKeys = {
  range: (
    bookId: string,
    startCp: number,
    endCp: number,
    readingMode: ReadingMode,
    horizon: number | null,
  ) => ['annotations', bookId, startCp, endCp, readingMode, horizon] as const,
}

export interface AnnotationQuery {
  startCp: number
  endCp: number
  readingMode?: ReadingMode
  visibleHorizonCp?: number | null
}

export function fetchAnnotations(
  bookId: string,
  query: AnnotationQuery,
  signal?: AbortSignal,
): Promise<AnnotationsResponse> {
  const params = new URLSearchParams()
  params.set('start_cp', String(query.startCp))
  params.set('end_cp', String(query.endCp))
  params.set('reading_mode', query.readingMode ?? 'initial')
  if (query.visibleHorizonCp !== null && query.visibleHorizonCp !== undefined) {
    params.set('visible_horizon_cp', String(query.visibleHorizonCp))
  }
  return apiData<AnnotationsResponse>(`/api/books/${bookId}/annotations?${params.toString()}`, {
    signal,
  })
}