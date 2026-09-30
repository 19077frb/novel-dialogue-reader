import { apiData, apiRequest } from './client'
import type { BookmarkOut, BookmarkCreate } from './types'

export const bookmarkKey = (bookId: string) => ['bookmarks', bookId] as const
export function fetchBookmarks(bookId: string, cursor: string | null, signal?: AbortSignal) {
  const params = new URLSearchParams({ limit: '100' })
  if (cursor) params.set('cursor', cursor)
  return apiData<{ items: BookmarkOut[]; next_cursor: string | null }>(`/api/books/${bookId}/bookmarks?${params}`, { signal })
}
export function addBookmark(bookId: string, input: BookmarkCreate) {
  return apiData<BookmarkOut>(`/api/books/${bookId}/bookmarks`, { method: 'POST', body: input })
}
export function editBookmark(bookId: string, item: BookmarkOut, note: string) {
  return apiData<BookmarkOut>(`/api/books/${bookId}/bookmarks/${item.id}`, { method: 'PATCH', body: { note, expected_version: item.version } })
}
export function deleteBookmark(bookId: string, item: BookmarkOut) {
  return apiRequest<void>(`/api/books/${bookId}/bookmarks/${item.id}?expected_version=${item.version}`, { method: 'DELETE' })
}
