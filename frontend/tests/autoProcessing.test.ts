import { beforeEach, expect, it, vi } from 'vitest'
import * as books from '../src/api/books'
import * as jobs from '../src/api/jobs'
import * as batch from '../src/components/BatchProcessor'
import { autoMessage, resetAutomaticProcessing, scheduleAutomaticProcessing, stopAutomaticProcessing } from '../src/processing/autoProcessing'
import { getProcessingPreferences } from '../src/processing/preferences'

vi.mock('../src/api/books', () => ({ fetchChapters: vi.fn(), fetchProcessingStatus: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ estimateRange: vi.fn() }))
vi.mock('../src/components/BatchProcessor', () => ({ hasBatchWork: vi.fn(), isBatchRunning: vi.fn(), requestBatchStop: vi.fn(), runBatchProcessing: vi.fn() }))
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
