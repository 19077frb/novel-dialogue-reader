import { render, screen, cleanup, act } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as books from '../src/api/books'
import * as characters from '../src/api/characters'
import * as jobs from '../src/api/jobs'
import { ApiError } from '../src/api/client'
import { BatchRetryControls, clearBatchProgress, hasBatchWork, retryBatchTask, runBatchProcessing, useBatchProgress } from '../src/components/BatchProcessor'
import { getProcessingPreferences } from '../src/processing/preferences'
import { waitForJobCompletion } from '../src/processing/jobCompletion'
import type { ChapterOut, EstimateOut, JobDetailOut } from '../src/api/types'

vi.mock('../src/api/books', () => ({ completeChapterProcessing: vi.fn(), fetchBook: vi.fn(), fetchChapters: vi.fn(), fetchProcessingStatus: vi.fn() }))
vi.mock('../src/api/characters', () => ({ analyzeCharacterRoster: vi.fn(), confirmCharacterRoster: vi.fn(), fetchCharacterRoster: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ createJob: vi.fn(), estimateRange: vi.fn(), freshIdempotencyKey: vi.fn(() => crypto.randomUUID()) }))
vi.mock('../src/processing/jobCompletion', () => ({ waitForJobCompletion: vi.fn(async job => job) }))

const chapter = { id: 'c1', title: '第一章', ordinal: 0, start_cp: 0, end_cp: 100, dialogue_processed: false } as ChapterOut
const estimate = { book_version_id: 'v1', windows: [
  { window_id: 'w1', ordinal: 1, target_count: 1, estimated_tokens: 10 },
  { window_id: 'w2', ordinal: 2, target_count: 1, estimated_tokens: 10 },
] } as unknown as EstimateOut
const job = (state: 'COMPLETED' | 'FAILED' | 'NEEDS_RECONCILIATION') => ({ id: crypto.randomUUID(), state, usage: { total_tokens: 10 }, unknown_usage_runs: 0, last_error: state === 'FAILED' ? '模拟失败' : null }) as unknown as JobDetailOut
const roster = { status: 'CONFIRMED', version: 1, candidates: [{ canonical_name: '女生', temp_ref: 'p1', aliases: [] }] }
const usage = vi.fn()
function Snapshot() { return <pre data-testid="snapshot">{JSON.stringify(useBatchProgress('b1'))}</pre> }
function readSnapshot() { return JSON.parse(screen.getByTestId('snapshot').textContent!) }
async function start() {
  await runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: 1000 }, onUsage: usage })
}
beforeEach(() => {
  vi.resetAllMocks(); clearBatchProgress('b1')
  vi.mocked(waitForJobCompletion).mockImplementation(async job => job)
  vi.mocked(books.fetchBook).mockResolvedValue({ active_version_id: 'v1' } as never)
  vi.mocked(books.fetchChapters).mockResolvedValue([chapter])
  vi.mocked(books.fetchProcessingStatus).mockResolvedValue({ active_jobs: 0 })
  vi.mocked(characters.fetchCharacterRoster).mockResolvedValue(roster as never)
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue(job('COMPLETED'))
  vi.mocked(characters.confirmCharacterRoster).mockResolvedValue(roster as never)
  vi.mocked(jobs.createJob).mockImplementation(async input => job(input.selectedWindowIds?.[0] === 'w1' ? 'FAILED' : 'COMPLETED'))
  vi.mocked(jobs.estimateRange).mockResolvedValue({ ...estimate, windows: estimate.windows!.map(window => ({ ...window, processing_status: window.window_id === 'w2' ? 'completed' : 'failed' })) })
})
afterEach(() => { cleanup(); clearBatchProgress('b1') })

it('retries only the failed window, reuses confirmed people and cumulative allowance, and clears obsolete failures', async () => {
  await start()
  render(<><Snapshot /><BatchRetryControls bookId="b1" /></>)
  expect(screen.getByRole('button', { name: /重试窗口 1/ })).toBeEnabled()
  expect(usage).toHaveBeenLastCalledWith(30)
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await act(() => retryBatchTask('b1', 'dialogue:c1:w1'))
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  expect(jobs.createJob).toHaveBeenCalledTimes(3)
  expect(jobs.createJob).toHaveBeenLastCalledWith(expect.objectContaining({ selectedWindowIds: ['w1'], budget: expect.objectContaining({ maxInputTokens: 10 }) }))
  expect(usage).toHaveBeenLastCalledWith(40)
  expect(books.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1')
  expect(readSnapshot().chapterStates.c1.state).toBe('processed')
  expect(readSnapshot().tasks.every((task: { state: string }) => task.state === 'completed')).toBe(true)
})

it('retries failed people first and continues only remaining windows', async () => {
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValueOnce(job('FAILED'))
  await start()
  expect(jobs.createJob).not.toHaveBeenCalled()
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await retryBatchTask('b1', 'roster:c1')
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(2)
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
  expect(jobs.createJob).toHaveBeenCalledWith(expect.objectContaining({ selectedWindowIds: ['w1'] }))
})

it('preserves other failed tasks when retrying one window and does not claim whole-chapter completion', async () => {
  vi.mocked(jobs.createJob).mockResolvedValue(job('FAILED'))
  await start()
  vi.mocked(jobs.estimateRange).mockResolvedValue(estimate)
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  vi.mocked(books.completeChapterProcessing).mockRejectedValue(new ApiError(409, { code: 'RESOURCE_CONFLICT', message: '仍有未处理对白' }))
  await retryBatchTask('b1', 'dialogue:c1:w1')
  render(<Snapshot />)
  expect(readSnapshot().tasks.find((task: { id: string }) => task.id === 'dialogue:c1:w2').state).toBe('failed')
  expect(readSnapshot().chapterStates.c1.state).toBe('failed')
  expect(readSnapshot().message).toContain('还有未完成窗口')
})

it('locks retry planning and refuses overlapping or ambiguous paid requests', async () => {
  await start()
  let release!: () => void
  vi.mocked(books.fetchProcessingStatus).mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ active_jobs: 0 }) }))
  const retry = retryBatchTask('b1', 'dialogue:c1:w1')
  expect(hasBatchWork()).toBe(true)
  await expect(retryBatchTask('b1', 'dialogue:c1:w1')).rejects.toThrow('等待')
  release(); await retry
  clearBatchProgress('b1')
  vi.mocked(jobs.createJob).mockResolvedValue(job('NEEDS_RECONCILIATION'))
  await start()
  await expect(retryBatchTask('b1', 'dialogue:c1:w1')).rejects.toThrow('不能直接重试')
})
