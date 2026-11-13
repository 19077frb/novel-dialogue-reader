import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef } from 'react'

import { fetchBook, queryKeys, saveReadingProgress } from '../api/books'
import { ApiError } from '../api/client'
import type { BookOut, ReadingMode } from '../api/types'

type Position = { cp: number; mode: ReadingMode }
const samePosition = (a: Position | null, b: Position | null) => a !== null && b !== null && a.cp === b.cp && a.mode === b.mode

/** 串行写入，滚动合并为最后一个位置；每次使用上次写入返回的新版本。 */
export function useReadingProgress(bookId: string, book: BookOut | undefined, onError: (message: string | null) => void) {
  const client = useQueryClient()
  const callback = useRef(onError)
  callback.current = onError
  const activeVersion = book?.active_version_id
  // Each asynchronous writer keeps its own book and original-text version, even
  // when the same mounted reader navigates to another book.
  const session = useMemo(() => ({
    bookId, targetVersion: activeVersion, book: undefined as BookOut | undefined,
    pending: null as Position | null, inFlight: null as Position | null,
    saved: null as Position | null, retries: 0, blocked: false, detached: false,
    timer: undefined as ReturnType<typeof setTimeout> | undefined,
  }), [bookId, activeVersion])
  const current = useRef(session)
  current.current = session
  if (book && (!session.book || book.version >= session.book.version)) {
    if (session.book && book.version > session.book.version &&
        (book.read_position_cp !== session.book.read_position_cp || book.reading_mode !== session.book.reading_mode)) session.saved = null
    session.book = book
  }

  const flush = useCallback(async function flushSession() {
    clearTimeout(session.timer)
    if (session.inFlight || session.blocked || !session.pending || !session.book || !session.targetVersion) return
    if (samePosition(session.pending, session.saved)) { session.pending = null; return }
    const input = session.pending
    session.pending = null
    session.inFlight = input
    const notify = (message: string | null) => {
      if (current.current === session && !session.detached) callback.current(message)
    }
    try {
      const send = () => saveReadingProgress(session.bookId, {
        bookVersionId: session.targetVersion!, readPositionCp: input.cp,
        readingMode: input.mode, expectedVersion: session.book!.version,
      })
      let result
      try { result = await send() } catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'VERSION_CONFLICT') throw error
        const refreshed = await fetchBook(session.bookId)
        if (session.blocked || refreshed.active_version_id !== session.targetVersion) {
          session.blocked = true
          session.pending = null
          throw new Error('原文版本已改变')
        }
        session.book = refreshed
        result = await send()
      }
      if (result.version < session.book!.version) {
        // A newer book refresh arrived while this response was in flight.
        // Keep its metadata/version and confirm the latest intent against it.
        session.saved = null
        if (!session.blocked) session.pending ??= input
      } else {
        session.saved = { cp: result.read_position_cp, mode: result.reading_mode }
        const next = { ...session.book!, read_position_cp: result.read_position_cp, reading_mode: result.reading_mode, version: result.version, read_position_version_id: result.book_version_id }
        session.book = next
        if (current.current === session && !session.detached) client.setQueryData(queryKeys.book(session.bookId), next)
      }
      void client.invalidateQueries({ queryKey: ['books'] })
      session.retries = 0
      notify(null)
    } catch {
      // A failed response may still have committed server-side. Do not treat
      // the previous saved position as confirmed after an uncertain write.
      session.saved = null
      session.retries = session.pending ? 0 : session.retries + 1
      if (!session.blocked) session.pending ??= input
      notify(session.blocked ? '原文版本已改变，请重新读取后继续阅读。' : session.retries < 3 ? '最后阅读位置保存失败，将自动重试。' : '最后阅读位置保存失败，请重新读取后重试。')
    } finally {
      session.inFlight = null
      if (session.pending && !session.blocked && session.retries < 3) {
        session.timer = setTimeout(() => void flushSession(), session.detached ? 0 : 1500)
      }
    }
  }, [session, client])
  const persist = useCallback((cp: number, mode: ReadingMode, immediate = false) => {
    if (session.blocked) return
    clearTimeout(session.timer)
    session.pending = { cp, mode }
    session.retries = 0
    // Returning to the saved position cancels an unsent scroll. An in-flight
    // different position still needs a compensating write after it completes.
    if (!session.inFlight && samePosition(session.pending, session.saved)) { session.pending = null; return }
    if (immediate) void flush()
    else session.timer = setTimeout(() => void flush(), 600)
  }, [session, flush])
  useEffect(() => {
    session.detached = false
    const hidden = () => { if (document.visibilityState === 'hidden') void flush() }
    document.addEventListener('visibilitychange', hidden)
    return () => {
      document.removeEventListener('visibilitychange', hidden)
      session.detached = true
      clearTimeout(session.timer)
      // Coordinates from an old original-text version must never be retried
      // after switching versions. A book navigation still flushes the old book.
      if (current.current.bookId === session.bookId && current.current.targetVersion !== session.targetVersion) {
        session.blocked = true
        session.pending = null
      } else void flush()
    }
  }, [session, flush])
  return persist
}
