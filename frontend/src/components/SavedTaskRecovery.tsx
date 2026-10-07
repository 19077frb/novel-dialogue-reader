import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { fetchBooks, queryKeys } from '../api/books'
import { restoreSavedSingles } from '../processing/singleWorkflow'
import { restoreSavedBatches } from './BatchProcessor'
import { ReadErrorNotice } from './ReadErrorNotice'
import { pumpQueue } from '../processing/workQueue'

/** Shared browser storage may belong to a different data directory on the same port. */
export function SavedTaskRecovery() {
  const queryClient = useQueryClient()
  const [hasRecords] = useState(() => Object.keys(localStorage).some(key =>
    key.startsWith('ndr:tasks:v1:batch:') || key.startsWith('ndr:tasks:v1:single:') || key === 'ndr:tasks:v1:admissions'))
  const restored = useRef(false)
  const [restoreError, setRestoreError] = useState<unknown>(null)
  useEffect(() => {
    const completed = (event: Event) => {
      const id = (event as CustomEvent<{ bookId: string }>).detail.bookId
      void queryClient.invalidateQueries({ queryKey: ['chapters', id] })
      void queryClient.invalidateQueries({ queryKey: ['book-characters', id] })
      void queryClient.invalidateQueries({ queryKey: ['character-directory', id] })
      void queryClient.invalidateQueries({ queryKey: ['usage', id] })
    }
    window.addEventListener('ndr:queue-completed', completed)
    const windowCompleted = (event: Event) => {
      const { bookId, startCp, endCp } = (event as CustomEvent<{ bookId: string; startCp: number; endCp: number }>).detail
      void queryClient.invalidateQueries({ predicate: query => query.queryKey[0] === 'annotations'
        && query.queryKey[1] === bookId && Number(query.queryKey[2]) < endCp && Number(query.queryKey[3]) > startCp })
      void queryClient.invalidateQueries({ queryKey: ['window-preview', bookId] })
    }
    window.addEventListener('ndr:window-completed', windowCompleted)
    return () => {
      window.removeEventListener('ndr:queue-completed', completed)
      window.removeEventListener('ndr:window-completed', windowCompleted)
    }
  }, [queryClient])
  const books = useQuery({
    queryKey: ['task-recovery-books'],
    enabled: hasRecords,
    staleTime: Infinity,
    queryFn: async ({ signal }) => {
      // Share the shelf's first page instead of reading it twice at startup.
      let page = await queryClient.fetchQuery({
        queryKey: queryKeys.books(), queryFn: ({ signal }) => fetchBooks(signal), staleTime: 5000,
      })
      const ids = new Set(page.items.map(book => book.id))
      const visited = new Set<string>()
      while (page.next_cursor) {
        if (visited.has(page.next_cursor)) throw new Error('书架分页异常，未恢复任务，请重新读取。')
        visited.add(page.next_cursor)
        page = await fetchBooks(signal, page.next_cursor)
        page.items.forEach(book => ids.add(book.id))
      }
      return ids
    },
  })
  useEffect(() => {
    if (!books.data || books.isError || restored.current) return
    try {
      restoreSavedBatches(books.data)
      restoreSavedSingles(books.data)
      void pumpQueue(books.data)
      restored.current = true
      setRestoreError(null)
    } catch (error) { setRestoreError(error) }
  }, [books.data, books.dataUpdatedAt, books.isError])

  const error = books.error ?? restoreError
  if (!error) return null
  return <ReadErrorNotice label="保存的任务暂未恢复" error={error} retrying={books.isFetching}
    onRetry={() => { void books.refetch() }} />
}
