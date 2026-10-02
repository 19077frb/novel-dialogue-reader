import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { fetchBooks, queryKeys } from '../api/books'
import { restoreSavedSingles } from '../processing/singleWorkflow'
import { restoreSavedBatches } from './BatchProcessor'
import { ReadErrorNotice } from './ReadErrorNotice'

/** Shared browser storage may belong to a different data directory on the same port. */
export function SavedTaskRecovery() {
  const queryClient = useQueryClient()
  const [hasRecords] = useState(() => Object.keys(localStorage).some(key =>
    key.startsWith('ndr:tasks:v1:batch:') || key.startsWith('ndr:tasks:v1:single:')))
  const restored = useRef(false)
  const [restoreError, setRestoreError] = useState<unknown>(null)
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
      restored.current = true
      setRestoreError(null)
    } catch (error) { setRestoreError(error) }
  }, [books.data, books.dataUpdatedAt, books.isError])

  const error = books.error ?? restoreError
  if (!error) return null
  return <ReadErrorNotice label="保存的任务暂未恢复" error={error} retrying={books.isFetching}
    onRetry={() => { void books.refetch() }} />
}
