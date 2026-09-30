import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../src/api/books'
import { ApiError } from '../src/api/client'
import type { BookOut, ReadingProgressOut } from '../src/api/types'
import { useReadingProgress } from '../src/hooks/useReadingProgress'

vi.mock('../src/api/books', () => ({ saveReadingProgress: vi.fn(), fetchBook: vi.fn(), queryKeys: { book: (id: string) => ['book', id] } }))
const book = { id: 'b', active_version_id: 'v', version: 3, read_position_cp: 0, reading_mode: 'initial' } as BookOut
function setup() {
  const client = new QueryClient()
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return renderHook(() => useReadingProgress('b', book, vi.fn()), { wrapper })
}
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks() })
afterEach(() => vi.useRealTimers())

it('coalesces scrolls and waits for the in-flight write before sending the newest position', async () => {
  let finish!: (value: ReadingProgressOut) => void
  vi.mocked(api.saveReadingProgress).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    .mockResolvedValue({ book_id: 'b', book_version_id: 'v', read_position_cp: 30, reading_mode: 'initial', version: 5 })
  const hook = setup()
  act(() => { hook.result.current(10, 'initial'); hook.result.current(20, 'initial') })
  await act(() => vi.advanceTimersByTimeAsync(600))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  expect(vi.mocked(api.saveReadingProgress).mock.calls[0][1].readPositionCp).toBe(20)
  act(() => hook.result.current(30, 'initial'))
  await act(() => vi.advanceTimersByTimeAsync(600))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  await act(async () => { finish({ book_id: 'b', book_version_id: 'v', read_position_cp: 20, reading_mode: 'initial', version: 4 }) })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(2)
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 30, expectedVersion: 4 })
  hook.unmount()
})

it('refreshes a conflicted version and retries the intended position instead of the old server position', async () => {
  vi.mocked(api.saveReadingProgress).mockRejectedValueOnce(new ApiError(409, { code: 'VERSION_CONFLICT', message: '冲突' }))
    .mockResolvedValue({ book_id: 'b', book_version_id: 'v', read_position_cp: 35, reading_mode: 'reread', version: 8 })
  vi.mocked(api.fetchBook).mockResolvedValue({ ...book, version: 7, read_position_cp: 1 })
  const hook = setup()
  await act(async () => hook.result.current(35, 'reread', true))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 35, readingMode: 'reread', expectedVersion: 7 })
  hook.unmount()
})
