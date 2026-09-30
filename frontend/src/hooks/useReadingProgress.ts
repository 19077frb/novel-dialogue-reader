import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'

import { fetchBook, queryKeys, saveReadingProgress } from '../api/books'
import { ApiError } from '../api/client'
import type { BookOut, ReadingMode } from '../api/types'

/** 串行写入，滚动合并为最后一个位置；每次使用上次写入返回的新版本。 */
export function useReadingProgress(bookId: string, book: BookOut | undefined, onError: (message: string | null) => void) {
  const client = useQueryClient()
  const bookRef = useRef(book)
  bookRef.current = book
  const pending = useRef<{ cp: number; mode: ReadingMode } | null>(null)
  const writing = useRef(false)
  const retries = useRef(0)
  const saved = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const callback = useRef(onError)
  callback.current = onError
  const flush = useCallback(async () => {
    if (writing.current || !pending.current || !bookRef.current?.active_version_id) return
    writing.current = true
    const targetVersion = bookRef.current.active_version_id
    const input = pending.current
    pending.current = null
    try {
      const send = () => saveReadingProgress(bookId, { bookVersionId: targetVersion, readPositionCp: input.cp, readingMode: input.mode, expectedVersion: bookRef.current!.version })
      let result
      try { result = await send() } catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'VERSION_CONFLICT') throw error
        bookRef.current = await fetchBook(bookId)
        if (bookRef.current.active_version_id !== targetVersion) throw new Error('原文版本已改变')
        result = await send()
      }
      saved.current = `${input.cp}:${input.mode}`
      const next = { ...bookRef.current!, read_position_cp: result.read_position_cp, reading_mode: result.reading_mode, version: result.version, read_position_version_id: result.book_version_id }
      bookRef.current = next
      client.setQueryData(queryKeys.book(bookId), next)
      void client.invalidateQueries({ queryKey: ['books'] })
      retries.current = 0
      callback.current(null)
    } catch {
      retries.current += 1
      pending.current ??= input
      callback.current(retries.current < 3 ? '最后阅读位置保存失败，将自动重试。' : '最后阅读位置保存失败，请重新读取后重试。')
    } finally {
      writing.current = false
      if (pending.current && retries.current < 3) timer.current = setTimeout(() => void flush(), 1500)
    }
  }, [bookId, client])
  const persist = useCallback((cp: number, mode: ReadingMode, immediate = false) => {
    if (`${cp}:${mode}` === saved.current) return
    pending.current = { cp, mode }
    retries.current = 0
    clearTimeout(timer.current)
    if (immediate) void flush()
    else timer.current = setTimeout(() => void flush(), 600)
  }, [flush])
  useEffect(() => {
    const hidden = () => { if (document.visibilityState === 'hidden') void flush() }
    document.addEventListener('visibilitychange', hidden)
    return () => { document.removeEventListener('visibilitychange', hidden); clearTimeout(timer.current); void flush() }
  }, [flush])
  return persist
}
