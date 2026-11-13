import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import { StrictMode, type PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import * as api from '../src/api/books'
import { ApiError } from '../src/api/client'
import type { BookOut, ReadingProgressOut } from '../src/api/types'
import { useReadingProgress } from '../src/hooks/useReadingProgress'

vi.mock('../src/api/books', () => ({ saveReadingProgress: vi.fn(), fetchBook: vi.fn(), queryKeys: { book: (id: string) => ['book', id] } }))
const book = { id: 'b', active_version_id: 'v', version: 3, read_position_cp: 0, reading_mode: 'initial' } as BookOut
const output = (cp: number, version = 4, mode: 'initial' | 'reread' = 'initial'): ReadingProgressOut => ({ book_id: 'b', book_version_id: 'v', read_position_cp: cp, reading_mode: mode, version })
function deferred() {
  let resolve!: (value: ReadingProgressOut) => void
  let reject!: (error: Error) => void
  const promise = new Promise<ReadingProgressOut>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function setup(strict = false) {
  const client = new QueryClient()
  const onError = vi.fn()
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{strict ? <StrictMode>{children}</StrictMode> : children}</QueryClientProvider>
  const hook = renderHook(({ id, data }) => useReadingProgress(id, data, onError), { wrapper, initialProps: { id: 'b', data: book } })
  return { ...hook, client, onError }
}
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks() })
afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers() })

it('coalesces scrolls and serializes the newest position using the returned version', async () => {
  const first = deferred()
  vi.mocked(api.saveReadingProgress).mockReturnValueOnce(first.promise).mockResolvedValue(output(30, 5))
  const hook = setup()
  act(() => { hook.result.current(10, 'initial'); hook.result.current(20, 'initial') })
  await act(() => vi.advanceTimersByTimeAsync(600))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  expect(vi.mocked(api.saveReadingProgress).mock.calls[0][1].readPositionCp).toBe(20)
  act(() => hook.result.current(30, 'initial'))
  await act(() => vi.advanceTimersByTimeAsync(600))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  await act(async () => { first.resolve(output(20)) })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 30, expectedVersion: 4 })
  hook.unmount()
})

it('cancels an unsent scroll when returning to the confirmed saved position', async () => {
  vi.mocked(api.saveReadingProgress).mockResolvedValue(output(10))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  act(() => { hook.result.current(20, 'initial'); hook.result.current(10, 'initial') })
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  hook.unmount()
})

it('compensates an in-flight scroll when returning to the previously saved position', async () => {
  const second = deferred()
  vi.mocked(api.saveReadingProgress).mockResolvedValueOnce(output(10)).mockReturnValueOnce(second.promise).mockResolvedValue(output(10, 6))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  await act(async () => hook.result.current(20, 'initial', true))
  act(() => hook.result.current(10, 'initial'))
  await act(async () => { second.resolve(output(20, 5)) })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  expect(vi.mocked(api.saveReadingProgress).mock.calls.map(call => call[1].readPositionCp)).toEqual([10, 20, 10])
  expect(vi.mocked(api.saveReadingProgress).mock.calls[2][1].expectedVersion).toBe(5)
  hook.unmount()
})

it('retries the latest return position after an uncertain failed write', async () => {
  const second = deferred()
  vi.mocked(api.saveReadingProgress).mockResolvedValueOnce(output(10)).mockReturnValueOnce(second.promise).mockResolvedValue(output(10, 5))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  await act(async () => hook.result.current(20, 'initial', true))
  act(() => hook.result.current(10, 'initial'))
  await act(async () => { second.reject(new Error('network')) })
  await act(() => vi.advanceTimersByTimeAsync(1500))
  expect(vi.mocked(api.saveReadingProgress).mock.calls.map(call => call[1].readPositionCp)).toEqual([10, 20, 10])
  hook.unmount()
})

it('refreshes conflicts without restoring the old server position', async () => {
  vi.mocked(api.saveReadingProgress).mockRejectedValueOnce(new ApiError(409, { code: 'VERSION_CONFLICT', message: '冲突' })).mockResolvedValue(output(35, 8, 'reread'))
  vi.mocked(api.fetchBook).mockResolvedValue({ ...book, version: 7, read_position_cp: 1 })
  const hook = setup()
  await act(async () => hook.result.current(35, 'reread', true))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 35, readingMode: 'reread', expectedVersion: 7 })
  hook.unmount()
})

it('stops old-coordinate retries when conflict refresh discovers a different text version', async () => {
  vi.mocked(api.saveReadingProgress).mockRejectedValue(new ApiError(409, { code: 'VERSION_CONFLICT', message: '冲突' }))
  vi.mocked(api.fetchBook).mockResolvedValue({ ...book, version: 7, active_version_id: 'v2' })
  const hook = setup()
  await act(async () => hook.result.current(35, 'initial', true))
  await act(() => vi.advanceTimersByTimeAsync(10000))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  expect(hook.onError).toHaveBeenLastCalledWith('原文版本已改变，请重新读取后继续阅读。')
  hook.unmount()
})

it('limits automatic retries and lets a new intent resume saving', async () => {
  vi.mocked(api.saveReadingProgress).mockRejectedValue(new Error('network'))
  const hook = setup()
  await act(async () => hook.result.current(20, 'initial', true))
  await act(() => vi.advanceTimersByTimeAsync(10000))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(3)
  vi.mocked(api.saveReadingProgress).mockResolvedValue(output(10))
  await act(async () => hook.result.current(10, 'initial', true))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(4)
  expect(hook.onError).toHaveBeenLastCalledWith(null)
  hook.unmount()
})

it('saves mode changes at the same position and does not regress versions from stale props', async () => {
  vi.mocked(api.saveReadingProgress).mockResolvedValueOnce(output(10)).mockResolvedValue(output(10, 5, 'reread'))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  hook.rerender({ id: 'b', data: { ...book } })
  await act(async () => hook.result.current(10, 'reread', true))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ expectedVersion: 4, readingMode: 'reread' })
  hook.unmount()
})

it('flushes the old book independently and isolates late results after switching books', async () => {
  const first = deferred()
  vi.mocked(api.saveReadingProgress).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ ...output(50), book_id: 'c', book_version_id: 'w' }).mockResolvedValue(output(20, 5))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  act(() => hook.result.current(20, 'initial'))
  hook.rerender({ id: 'c', data: { ...book, id: 'c', active_version_id: 'w' } })
  await act(async () => hook.result.current(50, 'initial', true))
  await act(async () => { first.resolve(output(10)) })
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[2]).toEqual(['b', { bookVersionId: 'v', readPositionCp: 20, readingMode: 'initial', expectedVersion: 4 }])
  expect(hook.client.getQueryData<BookOut>(['book', 'c'])?.read_position_cp).toBe(50)
  expect(hook.onError).toHaveBeenCalledTimes(1)
  hook.unmount()
})

it('drops old pending positions and isolates late responses when changing text versions', async () => {
  const first = deferred()
  vi.mocked(api.saveReadingProgress).mockReturnValueOnce(first.promise).mockResolvedValue({ ...output(50, 6), book_version_id: 'v2' })
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  act(() => hook.result.current(20, 'initial'))
  hook.rerender({ id: 'b', data: { ...book, active_version_id: 'v2', version: 5 } })
  await act(async () => hook.result.current(50, 'initial', true))
  await act(async () => { first.resolve(output(10)) })
  await act(() => vi.advanceTimersByTimeAsync(5000))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(2)
  expect(hook.client.getQueryData<BookOut>(['book', 'b'])).toMatchObject({ active_version_id: 'v2', read_position_cp: 50, version: 6 })
  expect(hook.onError).toHaveBeenCalledTimes(1)
  hook.unmount()
})

it('flushes on visibility change without leaving a duplicate timer', async () => {
  vi.mocked(api.saveReadingProgress).mockResolvedValue(output(10))
  const hook = setup()
  act(() => hook.result.current(10, 'initial'))
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  await act(async () => { document.dispatchEvent(new Event('visibilitychange')) })
  await act(() => vi.advanceTimersByTimeAsync(3000))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  hook.unmount()
})

it('flushes the final pending intent after an in-flight request completes following unmount', async () => {
  const first = deferred()
  vi.mocked(api.saveReadingProgress).mockReturnValueOnce(first.promise).mockResolvedValue(output(20, 5))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  act(() => hook.result.current(20, 'initial'))
  hook.unmount()
  await act(async () => { first.resolve(output(10)) })
  await act(() => vi.advanceTimersByTimeAsync(1))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 20, expectedVersion: 4 })
  expect(hook.onError).not.toHaveBeenCalled()
})

it('keeps saving after StrictMode effect cleanup and setup', async () => {
  vi.mocked(api.saveReadingProgress).mockResolvedValue(output(10))
  const hook = setup(true)
  await act(async () => hook.result.current(10, 'initial', true))
  expect(api.saveReadingProgress).toHaveBeenCalledTimes(1)
  expect(hook.onError).toHaveBeenLastCalledWith(null)
  hook.unmount()
})

it('reconfirms a previously saved position after a newer server refresh changes position or mode', async () => {
  vi.mocked(api.saveReadingProgress).mockResolvedValueOnce(output(10)).mockResolvedValueOnce(output(10, 6)).mockResolvedValue(output(10, 8))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  hook.rerender({ id: 'b', data: { ...book, version: 5, read_position_cp: 20 } })
  await act(async () => hook.result.current(10, 'initial', true))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 10, expectedVersion: 5 })
  hook.rerender({ id: 'b', data: { ...book, version: 7, read_position_cp: 10, reading_mode: 'reread' } })
  await act(async () => hook.result.current(10, 'initial', true))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[2][1]).toMatchObject({ readingMode: 'initial', expectedVersion: 7 })
  hook.unmount()
})

it('does not lower newer book data when an older write response arrives late', async () => {
  const first = deferred()
  vi.mocked(api.saveReadingProgress).mockReturnValueOnce(first.promise).mockResolvedValue(output(30, 9))
  const hook = setup()
  await act(async () => hook.result.current(10, 'initial', true))
  const refreshed = { ...book, version: 8, read_position_cp: 20, title: 'new title' }
  hook.client.setQueryData(['book', 'b'], refreshed)
  hook.rerender({ id: 'b', data: refreshed })
  act(() => hook.result.current(30, 'initial'))
  await act(async () => { first.resolve(output(10)) })
  expect(hook.client.getQueryData(['book', 'b'])).toEqual(refreshed)
  await act(() => vi.advanceTimersByTimeAsync(1500))
  expect(vi.mocked(api.saveReadingProgress).mock.calls[1][1]).toMatchObject({ readPositionCp: 30, expectedVersion: 8 })
  expect(hook.client.getQueryData<BookOut>(['book', 'b'])).toMatchObject({ version: 9, title: 'new title', read_position_cp: 30 })
  hook.unmount()
})
