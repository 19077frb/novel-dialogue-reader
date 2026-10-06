import { render, screen, cleanup, act } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as books from '../src/api/books'
import * as characters from '../src/api/characters'
import * as jobs from '../src/api/jobs'
import { ApiError } from '../src/api/client'
import { appendAutomaticProcessing, BatchRetryControls, canAppendAutomaticProcessing, cancelChapterProcessing, clearBatchProgress, hasBatchWork, requestBatchStop, retryBatchTask, retryChapterProcessing, runBatchProcessing, synchronizeManualChapterStatus, useBatchProgress } from '../src/components/BatchProcessor'
import { getProcessingPreferences, updateProcessingPreferences } from '../src/processing/preferences'
import { waitForJobCompletion } from '../src/processing/jobCompletion'
import type { ChapterOut, EstimateOut, JobDetailOut } from '../src/api/types'

vi.mock('../src/api/books', () => ({ completeChapterProcessing: vi.fn(), fetchBook: vi.fn(), fetchChapters: vi.fn(), fetchProcessingStatus: vi.fn(), setChapterProcessingStatus: vi.fn() }))
vi.mock('../src/api/characters', () => ({ analyzeCharacterRoster: vi.fn(), confirmCharacterRoster: vi.fn(), fetchCharacterRoster: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ pauseJob: vi.fn(), createJob: vi.fn(), estimateRange: vi.fn(), freshIdempotencyKey: vi.fn(() => crypto.randomUUID()) }))
vi.mock('../src/processing/jobCompletion', () => ({ waitForJobCompletion: vi.fn(async job => job) }))

const chapter = { id: 'c1', title: '第一章', ordinal: 0, start_cp: 0, end_cp: 100, dialogue_processed: false } as ChapterOut
const estimate = { book_version_id: 'v1', windows: [
  { window_id: 'w1', ordinal: 1, target_count: 1, estimated_tokens: 10 },
  { window_id: 'w2', ordinal: 2, target_count: 1, estimated_tokens: 10 },
] } as unknown as EstimateOut
const job = (state: 'COMPLETED' | 'FAILED' | 'NEEDS_RECONCILIATION') => ({ id: crypto.randomUUID(), state, usage: { input_tokens: 7, output_tokens: 3 }, unknown_usage_runs: 0, last_error: state === 'FAILED' ? '模拟失败' : null }) as unknown as JobDetailOut
const roster = { status: 'CONFIRMED', version: 1, candidates: [{ canonical_name: '女生', temp_ref: 'p1', aliases: [] }] }
const usage = vi.fn()
function Snapshot() { return <pre data-testid="snapshot">{JSON.stringify(useBatchProgress('b1'))}</pre> }
function readSnapshot() { return JSON.parse(screen.getByTestId('snapshot').textContent!) }
async function start() {
  await runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: 1000 }, onUsage: usage })
}
beforeEach(() => {
  localStorage.clear()
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

it('readmits a manually cancelled queued chapter into the same pool without duplicate tasks or counts', async () => {
  let finishFirst!: (value: JobDetailOut) => void
  vi.mocked(characters.analyzeCharacterRoster).mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  vi.mocked(books.setChapterProcessingStatus).mockImplementation(async (_book, id, _version, processed) => ({
    ...chapter, id, processing_status_override: processed,
  }))
  const second = { ...chapter, id: 'c2', ordinal: 1 }
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter, second],
    plans: [{ chapter, estimate }, { chapter: second, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 2, tokenLimit: null }, expandable: true })
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1))
  await cancelChapterProcessing('b1', 'c2')
  synchronizeManualChapterStatus('b1', { ...second, dialogue_processed: true, processing_status_override: true })
  synchronizeManualChapterStatus('b1', { ...second, dialogue_processed: false, processing_status_override: false })
  expect(appendAutomaticProcessing('b1', 'v1', [{ chapter: { ...second, processing_status_override: false }, estimate }])).toBe(1)
  expect(appendAutomaticProcessing('b1', 'v1', [{ chapter: second, estimate }])).toBe(0)
  render(<Snapshot />)
  expect(readSnapshot().tasks.filter((task: { chapterId: string }) => task.chapterId === 'c2')).toHaveLength(3)
  expect(readSnapshot().chapterStates.c2.manualStatusCleared).toBe(true)
  finishFirst(job('COMPLETED')); await act(() => run)
  expect(vi.mocked(characters.analyzeCharacterRoster).mock.calls.map(call => call[1])).toEqual(['c1', 'c2'])
  expect(readSnapshot().message).toContain('新处理 2 章')
  expect(readSnapshot().message).not.toContain('已取消')
  expect(readSnapshot().message).not.toContain('跳过已处理')
})

it('waits for a cancelled model request to reach its terminal state before readmitting the chapter', async () => {
  let finishFirst!: (value: JobDetailOut) => void
  let finishSecond!: (value: JobDetailOut) => void
  vi.mocked(characters.analyzeCharacterRoster)
    .mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
    .mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve }))
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  const second = { ...chapter, id: 'c2', ordinal: 1 }
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter, second],
    plans: [{ chapter, estimate }, { chapter: second, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 2, tokenLimit: null }, expandable: true })
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1))
  await cancelChapterProcessing('b1', 'c1')
  expect(appendAutomaticProcessing('b1', 'v1', [{ chapter, estimate }])).toBe(0)
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  finishFirst(job('COMPLETED'))
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(2))
  expect(appendAutomaticProcessing('b1', 'v1', [{ chapter, estimate }])).toBe(1)
  finishSecond(job('COMPLETED')); await run
  expect(vi.mocked(characters.analyzeCharacterRoster).mock.calls.map(call => call[1])).toEqual(['c1', 'c2', 'c1'])
  expect(jobs.pauseJob).toHaveBeenCalledTimes(1)
})

it('sums people, failed and successful dialogue attempts with input/output-only usage', async () => {
  await start()
  render(<Snapshot />)
  expect(usage).toHaveBeenLastCalledWith(30)
  expect(readSnapshot().message).toContain('本次累计 30 tokens')
})

it('uses the reported total rather than double-counting input and output', async () => {
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue({ ...job('COMPLETED'),
    usage: { input_tokens: 7, output_tokens: 3, total_tokens: 12 } })
  await start()
  expect(usage).toHaveBeenLastCalledWith(32)
})

it('refreshes allowance without erasing cumulative usage and preserves it for retries', async () => {
  const prompt = vi.spyOn(window, 'prompt').mockReturnValue('0')
  try {
    vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue({ ...job('COMPLETED'), usage: { total_tokens: 8000 } })
    await runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
      preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: 10000 }, onUsage: usage })
    render(<Snapshot />)
    expect(prompt).toHaveBeenCalledTimes(1)
    expect(usage).toHaveBeenLastCalledWith(20)
    expect(readSnapshot().message).toContain(`本次累计 ${(8020).toLocaleString()} tokens`)
    vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
    await act(() => retryBatchTask('b1', 'dialogue:c1:w1'))
    expect(usage).toHaveBeenLastCalledWith(30)
    expect(readSnapshot().message).toContain(`本次累计 ${(8030).toLocaleString()} tokens`)
  } finally { prompt.mockRestore() }
})

it('reports unknown usage separately instead of claiming a complete zero-cost total', async () => {
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue({ ...job('COMPLETED'),
    usage: { input_tokens: 0, output_tokens: 0, unknown_runs: 1 }, unknown_usage_runs: 1 })
  await runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null } })
  render(<Snapshot />)
  expect(readSnapshot().message).toContain('本次已知累计 20 tokens')
  expect(readSnapshot().message).toContain('另有 1 次调用用量未知，未计入')
})

it('解除手动未处理保护后才启动新的批次，恢复正常完成状态', async () => {
  const manual = { ...chapter, processing_status_override: false }
  vi.mocked(books.setChapterProcessingStatus).mockResolvedValue({ ...manual, processing_status_override: null })
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [manual], plans: [{ chapter: manual, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null } })
  expect(books.setChapterProcessingStatus).toHaveBeenCalledWith('b1', 'c1', 'v1', null)
  expect(vi.mocked(books.setChapterProcessingStatus).mock.invocationCallOrder[0]).toBeLessThan(
    vi.mocked(characters.analyzeCharacterRoster).mock.invocationCallOrder[0])
  render(<Snapshot />)
  expect(readSnapshot().chapterStates.c1.state).toBe('processed')
})

it('records a textless chapter as completed and refreshes its persistent catalog status without dialogue calls', async () => {
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue({
    ...job('COMPLETED'), usage: { total_tokens: 0 },
    progress: { skipped_reason: 'no_text', calls: 0 },
  } as JobDetailOut)
  vi.mocked(characters.fetchCharacterRoster).mockResolvedValue({
    status: 'CONFIRMED', version: 1, candidates: [], confirmed_characters: [], pov_character_id: null,
  } as never)
  await start()
  render(<Snapshot />)
  expect(readSnapshot().chapterStates.c1.state).toBe('processed')
  expect(readSnapshot().chapterStates.c1.error).toBeNull()
  expect(readSnapshot().catalogRevision).toBeGreaterThan(0)
  expect(jobs.createJob).not.toHaveBeenCalled()
  expect(characters.confirmCharacterRoster).not.toHaveBeenCalled()
  expect(usage).toHaveBeenLastCalledWith(0)
})

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

it('keeps original roster repair settings on failed-task retry after preferences change', async () => {
  updateProcessingPreferences({ rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 3 })
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValueOnce(job('FAILED'))
  await start()
  updateProcessingPreferences({ rosterRepairEnabled: false, maxRosterRepairs: 0, maxFormatRetries: 0 })
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await retryBatchTask('b1', 'roster:c1')
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(2)
  for (const [, , input] of vi.mocked(characters.analyzeCharacterRoster).mock.calls) {
    expect(input).toMatchObject({ rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 3 })
  }
})

it('chapter retry fills failed tasks without repeating successful windows or adding unselected windows', async () => {
  await start()
  vi.mocked(jobs.estimateRange).mockResolvedValue({ ...estimate, windows: [
    ...estimate.windows!.map(window => ({ ...window, processing_status: 'failed' })),
    { ...estimate.windows![0], window_id: 'w3', ordinal: 3, processing_status: 'unprocessed' },
  ] } as EstimateOut)
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await retryChapterProcessing('b1', 'c1')
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  expect(jobs.createJob).toHaveBeenCalledTimes(3)
  expect(jobs.createJob).toHaveBeenLastCalledWith(expect.objectContaining({ selectedWindowIds: ['w1'] }))
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

it('extends the same pool before old dialogue finishes, deduplicates and sequences appended people', async () => {
  let finishFirstRoster!: (job: JobDetailOut) => void
  let finishOldWindow!: (job: JobDetailOut) => void
  vi.mocked(characters.analyzeCharacterRoster).mockImplementationOnce(() => new Promise(resolve => { finishFirstRoster = resolve }))
  vi.mocked(jobs.createJob).mockImplementation(async input => {
    if (input.selectedWindowIds?.[0] === 'w1') return new Promise(resolve => { finishOldWindow = resolve })
    return job('COMPLETED')
  })
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 3, tokenLimit: null }, expandable: true })
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1))
  const extra = [3, 2].map(ordinal => ({ chapter: { ...chapter, id: `c${ordinal}`, ordinal },
    estimate: { ...estimate, windows: [{ ...estimate.windows![0], window_id: `w${ordinal + 1}` }] } }))
  expect(appendAutomaticProcessing('b1', 'v1', [{ ...extra[0], estimate: { ...estimate, policy: { full_source: true } } }])).toBe(0)
  expect(appendAutomaticProcessing('b1', 'v1', extra)).toBe(2)
  expect(appendAutomaticProcessing('b1', 'v1', extra)).toBe(0)
  expect(appendAutomaticProcessing('b1', 'other-version', extra)).toBe(0)
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  finishFirstRoster(job('COMPLETED'))
  await vi.waitFor(() => expect(jobs.createJob).toHaveBeenCalledWith(expect.objectContaining({ selectedWindowIds: ['w4'] })))
  expect(vi.mocked(characters.analyzeCharacterRoster).mock.calls.map(call => call[1])).toEqual(['c1', 'c2', 'c3'])
  expect(hasBatchWork()).toBe(true)
  finishOldWindow(job('COMPLETED')); await run
  expect(canAppendAutomaticProcessing('b1', 'v1')).toBe(false)
  expect(books.completeChapterProcessing).toHaveBeenCalledTimes(3)
})

it.each(['complete', 'complete-blocks'] as const)('keeps %s block-version frozen when extending an automatic batch', async dialogueStrategy => {
  let finishFirstRoster!: (job: JobDetailOut) => void
  vi.mocked(characters.analyzeCharacterRoster).mockImplementationOnce(() => new Promise(resolve => { finishFirstRoster = resolve }))
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  const blocks = dialogueStrategy === 'complete-blocks'
  const policy = { full_source: true, ...(blocks ? { dialogue_blocks: true } : {}) }
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate: { ...estimate, policy } }],
    preferences: { ...getProcessingPreferences(), dialogueStrategy, profileId: 'p1', concurrency: 2, tokenLimit: null }, expandable: true })
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1))
  const next = { chapter: { ...chapter, id: 'c2', ordinal: 1 }, estimate: { ...estimate, policy } }
  expect(appendAutomaticProcessing('b1', 'v1', [{ ...next, estimate: { ...estimate, policy: { full_source: true, dialogue_blocks: !blocks } } }])).toBe(0)
  expect(appendAutomaticProcessing('b1', 'v1', [next])).toBe(1)
  finishFirstRoster(job('COMPLETED'))
  await run
  expect(books.completeChapterProcessing).toHaveBeenCalledTimes(2)
})

it('stops admission and dispatch of appended chapters when the user stops the queue', async () => {
  let finishOldWindow!: (job: JobDetailOut) => void
  vi.mocked(jobs.createJob).mockImplementationOnce(() => new Promise(resolve => { finishOldWindow = resolve }))
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null }, expandable: true })
  await vi.waitFor(() => expect(jobs.createJob).toHaveBeenCalledTimes(1))
  const extra = [{ chapter: { ...chapter, id: 'c2', ordinal: 1 }, estimate }]
  expect(appendAutomaticProcessing('b1', 'v1', extra)).toBe(1)
  requestBatchStop('b1')
  expect(appendAutomaticProcessing('b1', 'v1', [{ ...extra[0], chapter: { ...chapter, id: 'c3' } }])).toBe(0)
  finishOldWindow(job('COMPLETED')); await run
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
  expect(hasBatchWork()).toBe(false)
})

it('retries all unfinished windows of a chapter while another chapter is still running', async () => {
  const c2 = { ...chapter, id: 'c2', ordinal: 1 }
  let finishOther!: (job: JobDetailOut) => void
  vi.mocked(jobs.createJob).mockImplementation(async input => {
    if (input.range.chapterId === 'c2') return new Promise(resolve => { finishOther = resolve })
    return job('FAILED')
  })
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter, c2],
    plans: [{ chapter, estimate }, { chapter: c2, estimate: { ...estimate, windows: [estimate.windows![0]] } }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 3, tokenLimit: null } })
  await vi.waitFor(() => expect(jobs.createJob).toHaveBeenCalledTimes(3))
  vi.mocked(jobs.estimateRange).mockResolvedValue({ ...estimate, windows: estimate.windows!.map(window => ({ ...window, processing_status: 'failed' })) })
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  await retryChapterProcessing('b1', 'c1')
  await vi.waitFor(() => expect(jobs.createJob).toHaveBeenCalledTimes(5))
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(2)
  expect(hasBatchWork()).toBe(true)
  finishOther(job('COMPLETED')); await run
  render(<Snapshot />)
  expect(readSnapshot().chapterStates.c1.state).toBe('processed')
  expect(readSnapshot().chapterStates.c2.state).toBe('processed')
})

it('cancels every running window in one chapter, keeps other chapters running and retains usage', async () => {
  const c2 = { ...chapter, id: 'c2', ordinal: 1 }
  const finishers: Array<() => void> = []
  vi.mocked(jobs.createJob).mockImplementation(async input => ({ ...job('COMPLETED'),
    id: `${input.range.chapterId}:${input.selectedWindowIds![0]}`, state: input.range.chapterId === 'c1' ? 'RUNNING' : 'COMPLETED' }))
  vi.mocked(waitForJobCompletion).mockImplementation(async current => current.state === 'RUNNING'
    ? new Promise(resolve => { finishers.push(() => resolve({ ...current, state: 'COMPLETED' })) }) : current)
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter, c2],
    plans: [{ chapter, estimate }, { chapter: c2, estimate: { ...estimate, windows: [estimate.windows![0]] } }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 4, tokenLimit: null }, onUsage: usage })
  await vi.waitFor(() => expect(finishers).toHaveLength(2))
  await cancelChapterProcessing('b1', 'c1')
  expect(jobs.pauseJob).toHaveBeenCalledTimes(2)
  expect(jobs.pauseJob).toHaveBeenCalledWith('c1:w1')
  expect(jobs.pauseJob).toHaveBeenCalledWith('c1:w2')
  await expect(retryChapterProcessing('b1', 'c1')).rejects.toThrow('收尾')
  finishers.forEach(finish => finish()); await run
  expect(books.completeChapterProcessing).not.toHaveBeenCalledWith('b1', 'c1', 'v1')
  expect(books.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c2', 'v1')
  expect(usage).toHaveBeenLastCalledWith(50)
  render(<Snapshot />)
  expect(readSnapshot().chapterStates.c1.state).toBe('stopped')
  expect(readSnapshot().chapterStates.c1.cancelRequested).toBe(false)
  expect(readSnapshot().chapterStates.c2.state).toBe('processed')
})

it('cancels a queued chapter before people are dispatched, without stopping following chapters', async () => {
  let finishFirst!: (job: JobDetailOut) => void
  vi.mocked(characters.analyzeCharacterRoster).mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
  vi.mocked(jobs.createJob).mockResolvedValue(job('COMPLETED'))
  const extra = [2, 3].map(ordinal => ({ ...chapter, id: `c${ordinal}`, ordinal }))
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter, ...extra],
    plans: [chapter, ...extra].map(chapter => ({ chapter, estimate })),
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null } })
  await vi.waitFor(() => expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1))
  await cancelChapterProcessing('b1', 'c2')
  finishFirst(job('COMPLETED')); await run
  expect(vi.mocked(characters.analyzeCharacterRoster).mock.calls.map(call => call[1])).toEqual(['c1', 'c3'])
  expect(jobs.pauseJob).not.toHaveBeenCalled()
  expect(jobs.createJob).toHaveBeenCalledTimes(4)
})

it('pauses a submission that returns after cancellation and refuses to replay an ambiguous result', async () => {
  let finishSubmission!: (job: JobDetailOut) => void
  vi.mocked(jobs.createJob).mockImplementationOnce(() => new Promise(resolve => { finishSubmission = resolve }))
  const run = runBatchProcessing({ bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null } })
  await vi.waitFor(() => expect(jobs.createJob).toHaveBeenCalledTimes(1))
  await cancelChapterProcessing('b1', 'c1')
  expect(jobs.pauseJob).not.toHaveBeenCalled()
  const uncertain = job('NEEDS_RECONCILIATION')
  finishSubmission(uncertain); await run
  expect(jobs.pauseJob).toHaveBeenCalledWith(uncertain.id)
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
  await expect(retryChapterProcessing('b1', 'c1')).rejects.toThrow('不明确')
})
