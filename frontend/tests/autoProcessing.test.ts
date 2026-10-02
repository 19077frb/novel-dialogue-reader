import { beforeEach, expect, it, vi } from 'vitest'
import * as books from '../src/api/books'
import * as jobs from '../src/api/jobs'
import * as batch from '../src/components/BatchProcessor'
import { autoMessage, notifyManualChapterStatus, resetAutomaticProcessing, scheduleAutomaticProcessing, stopAutomaticProcessing } from '../src/processing/autoProcessing'
import { getProcessingPreferences } from '../src/processing/preferences'

vi.mock('../src/api/books', () => ({ fetchChapters: vi.fn(), fetchProcessingStatus: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ estimateRange: vi.fn() }))
vi.mock('../src/components/BatchProcessor', () => ({ appendAutomaticProcessing: vi.fn(), canAppendAutomaticProcessing: vi.fn(), hasBatchWork: vi.fn(), hasUnresolvedChapterResult: vi.fn(), isBatchRunning: vi.fn(), requestBatchStop: vi.fn(), runBatchProcessing: vi.fn() }))
const chapters = [0, 1, 2, 3].map(index => ({ id: `c${index}`, ordinal: index, title: `第${index}章`, start_cp: index * 100, end_cp: index * 100 + 100, dialogue_processed: index === 0 }))
const preferences = { ...getProcessingPreferences(), profileId: 'p1', concurrency: 2, tokenLimit: null }
beforeEach(() => {
  vi.resetAllMocks()
  for (const id of ['b1', 'b2', 'b3']) resetAutomaticProcessing(id)
  vi.mocked(books.fetchProcessingStatus).mockResolvedValue({ active_jobs: 0 })
  vi.mocked(books.fetchChapters).mockResolvedValue(chapters)
  vi.mocked(jobs.estimateRange).mockResolvedValue({ total_tokens: 100, windows: [] } as never)
  vi.mocked(batch.runBatchProcessing).mockImplementation(async input => { input.onUsage?.(25); input.onFinished?.() })
})

it('selects the current and next N chapters, skips completed and attempted chapters, carries usage', async () => {
  await scheduleAutomaticProcessing('b1', 'v1', 'c0', 2, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledWith(expect.objectContaining({ plans: [
    expect.objectContaining({ chapter: chapters[1] }), expect.objectContaining({ chapter: chapters[2] }),
  ] }))
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 2, preferences)
  expect(batch.runBatchProcessing).toHaveBeenLastCalledWith(expect.objectContaining({ initialSpent: 25, plans: [expect.objectContaining({ chapter: chapters[3] })] }))
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 2, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(2)
})

it('waits for manual jobs and never overlaps another automatic dispatch', async () => {
  vi.mocked(books.fetchProcessingStatus).mockResolvedValueOnce({ active_jobs: 1 })
  await scheduleAutomaticProcessing('b2', 'v1', 'c1', 1, preferences)
  expect(batch.runBatchProcessing).not.toHaveBeenCalled()
  let release!: () => void
  vi.mocked(batch.runBatchProcessing).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const first = scheduleAutomaticProcessing('b2', 'v1', 'c1', 1, preferences)
  await vi.waitFor(() => expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1))
  await scheduleAutomaticProcessing('b3', 'v1', 'c1', 1, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1)
  stopAutomaticProcessing()
  release(); await first
  expect(autoMessage('b2')).toContain('停止')
  await scheduleAutomaticProcessing('b2', 'v1', 'c3', 0, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1)
})

it('manual unprocessed clears only that chapter admission record, preserving usage and other attempts', async () => {
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 1, preferences)
  notifyManualChapterStatus('b1', 'v1', 'c1', true)
  notifyManualChapterStatus('b1', 'v1', 'c1', false)
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 1, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(2)
  expect(batch.runBatchProcessing).toHaveBeenLastCalledWith(expect.objectContaining({ initialSpent: 25,
    plans: [expect.objectContaining({ chapter: chapters[1] })] }))
})

it('rechecks a manual unprocessed chapter after its cancelled in-flight work finally releases admission', async () => {
  let release!: () => void
  vi.mocked(batch.runBatchProcessing).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const first = scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  await vi.waitFor(() => expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1))
  vi.mocked(batch.canAppendAutomaticProcessing).mockReturnValue(true)
  vi.mocked(batch.hasBatchWork).mockReturnValue(true)
  notifyManualChapterStatus('b1', 'v1', 'c1', false)
  vi.mocked(batch.appendAutomaticProcessing).mockReturnValueOnce(0).mockReturnValue(1)
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  expect(batch.appendAutomaticProcessing).toHaveBeenCalledTimes(2)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1)
  release(); await first
})

it('does not submit stale estimates when the user marks a chapter completed during planning', async () => {
  let finishEstimate!: (value: never) => void
  vi.mocked(jobs.estimateRange).mockImplementationOnce(() => new Promise(resolve => { finishEstimate = resolve }))
  const planning = scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  await vi.waitFor(() => expect(jobs.estimateRange).toHaveBeenCalledTimes(1))
  notifyManualChapterStatus('b1', 'v1', 'c1', true)
  finishEstimate({ total_tokens: 100, windows: [] } as never)
  await planning
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  expect(batch.runBatchProcessing).not.toHaveBeenCalled()
})

it('manual unprocessed never blindly replays a request whose outcome is unclear', async () => {
  vi.mocked(batch.hasUnresolvedChapterResult).mockReturnValue(true)
  notifyManualChapterStatus('b1', 'v1', 'c1', false)
  await scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  expect(batch.runBatchProcessing).not.toHaveBeenCalled()
  expect(autoMessage('b1')).toContain('结果尚不明确')
})

it('blocks at the cumulative limit and does not automatically retry paid failures', async () => {
  const limited = { ...preferences, tokenLimit: 25 }
  await scheduleAutomaticProcessing('b3', 'v1', 'c1', 0, limited)
  await scheduleAutomaticProcessing('b3', 'v1', 'c2', 0, limited)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1)
  expect(autoMessage('b3')).toContain('额度已用完')
  resetAutomaticProcessing('b3')
  vi.mocked(batch.runBatchProcessing).mockImplementationOnce(async input => { input.onError?.('模拟模型失败') })
  await scheduleAutomaticProcessing('b3', 'v1', 'c2', 0, preferences)
  await scheduleAutomaticProcessing('b3', 'v1', 'c2', 0, preferences)
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(2)
  expect(autoMessage('b3')).toBe('模拟模型失败')
})

it('appends newly visible chapters while the current pool is still busy without starting another batch', async () => {
  let release!: () => void
  vi.mocked(batch.runBatchProcessing).mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  const first = scheduleAutomaticProcessing('b1', 'v1', 'c1', 0, preferences)
  await vi.waitFor(() => expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1))
  vi.mocked(batch.canAppendAutomaticProcessing).mockReturnValue(true)
  vi.mocked(batch.hasBatchWork).mockReturnValue(true)
  vi.mocked(batch.appendAutomaticProcessing).mockReturnValue(1)
  vi.mocked(books.fetchProcessingStatus).mockResolvedValue({ active_jobs: 2 })
  await scheduleAutomaticProcessing('b1', 'v1', 'c2', 0, preferences)
  expect(batch.appendAutomaticProcessing).toHaveBeenCalledWith('b1', 'v1', [expect.objectContaining({ chapter: chapters[2] })])
  expect(batch.runBatchProcessing).toHaveBeenCalledTimes(1)
  await scheduleAutomaticProcessing('b1', 'v1', 'c2', 0, preferences)
  expect(batch.appendAutomaticProcessing).toHaveBeenCalledTimes(1)
  release(); await first
})
