import { beforeEach, expect, it, vi } from 'vitest'
import type { SingleWorkflow } from '../src/processing/singleWorkflow'
import type { JobDetailOut } from '../src/api/types'

const api = vi.hoisted(() => ({ fetchBook: vi.fn(), fetchJob: vi.fn(), fetchChapters: vi.fn(), completeChapterProcessing: vi.fn(),
  createJob: vi.fn(), fetchRecentJobs: vi.fn(), fetchRoster: vi.fn(), analyzeRoster: vi.fn(), confirmRoster: vi.fn(), pauseJob: vi.fn(), waitJob: vi.fn() }))
vi.mock('../src/api/books', () => ({ ...api, fetchProcessingStatus: vi.fn(), setChapterProcessingStatus: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ ...api, estimateRange: vi.fn(), freshIdempotencyKey: () => crypto.randomUUID() }))
vi.mock('../src/api/characters', () => ({ fetchCharacterRoster: api.fetchRoster,
  analyzeCharacterRoster: api.analyzeRoster, confirmCharacterRoster: api.confirmRoster }))
vi.mock('../src/processing/jobCompletion', () => ({
  TERMINAL_JOB_STATES: new Set(['COMPLETED', 'FAILED', 'PAUSED', 'PARTIAL', 'NEEDS_RECONCILIATION', 'BUDGET_EXHAUSTED']),
  waitForJobCompletion: (job: never, update: (job: never) => void) => api.waitJob(job, update),
}))
const completed = (id: string, tokens = 10): JobDetailOut => ({ id, kind: 'INFERENCE', state: 'COMPLETED',
  usage: { total_tokens: tokens }, unknown_usage_runs: 0, remaining_windows: 0, windows_total: 0,
  calls: 1, cached_windows: 0, created_at: '2026-10-02T00:00:00Z', updated_at: '2026-10-02T00:00:01Z' })
beforeEach(() => {
  vi.resetModules(); vi.resetAllMocks(); localStorage.clear()
  api.fetchBook.mockResolvedValue({ active_version_id: 'v1' })
  api.fetchChapters.mockResolvedValue([])
  api.waitJob.mockImplementation(async (job, update) => { update(job); return job })
  api.fetchJob.mockImplementation(async id => completed(id))
  api.fetchRecentJobs.mockResolvedValue([])
  api.createJob.mockImplementation(async input => {
    expect(Object.values(localStorage).some(raw => raw.includes(input.idempotencyKey))).toBe(true)
    return completed(`new:${input.selectedWindowIds?.[0]}`)
  })
  api.fetchRoster.mockResolvedValue({ status: 'CONFIRMED', candidates: [{ temp_ref: 'p1', canonical_name: '悠太', aliases: [] }], version: 1 })
})
function single(): SingleWorkflow {
  const input = { bookId: 'b1', bookVersionId: 'v1', mode: 'process' as const, range: { chapterId: 'c1', startCp: 0, endCp: 100 },
    profileId: 'p1', readingMode: 'reread' as const, visibleHorizonCp: null, budget: { maxInputTokens: 100, maxOutputTokens: 100, maxRecheckRounds: 0 }, runNow: true }
  return { bookId: 'b1', versionId: 'v1', chapterId: 'c1', startCp: 0, endCp: 100, mode: 'process', concurrency: 1,
    running: true, error: null, completeChapter: true,
    tasks: [
      { windowId: 'w1', ordinal: '1', input: { ...input, selectedWindowIds: ['w1'], idempotencyKey: 'old-key' }, job: completed('old'), error: null },
      { windowId: 'w2', ordinal: '2', input: { ...input, selectedWindowIds: ['w2'], idempotencyKey: 'next-key' }, job: null, error: null },
    ] }
}

it('persists a stop received before POST acknowledgement and does not dispatch later windows', async () => {
  const { runSingleWorkflow, stopSingleWorkflow } = await import('../src/processing/singleWorkflow')
  const work = single()
  work.tasks[0].job = null
  let acknowledge!: (job: JobDetailOut) => void
  api.createJob.mockImplementationOnce(() => new Promise(resolve => { acknowledge = resolve }))
  api.pauseJob.mockResolvedValue({ ...completed('accepted'), state: 'PAUSED' })
  const execution = runSingleWorkflow(work)
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => api.createJob.mock.calls.length === 1)
  await stopSingleWorkflow('b1')
  expect(JSON.parse(localStorage.getItem('ndr:tasks:v1:single:b1')!).stopRequested).toBe(true)
  acknowledge({ ...completed('accepted'), state: 'QUEUED' })
  await ended
  expect(api.pauseJob).toHaveBeenCalledWith('accepted')
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(api.completeChapterProcessing).not.toHaveBeenCalled()
})

it('restores stopped single ranges by reads only without posting pending windows', async () => {
  const { runSingleWorkflow } = await import('../src/processing/singleWorkflow')
  const work = { ...single(), stopRequested: true }
  await expect(runSingleWorkflow(work)).rejects.toThrow('安全停止')
  expect(api.createJob).not.toHaveBeenCalled()
  expect(api.fetchRecentJobs).toHaveBeenCalledWith(expect.objectContaining({ idempotencyKey: 'next-key' }))
})
it('ignores missing-book records without reading details, creating jobs or removing old records', async () => {
  localStorage.setItem('ndr:tasks:v1:single:other-library', 'invalid legacy data')
  localStorage.setItem('ndr:tasks:v1:batch:deleted-book', 'invalid legacy data')
  const { restoreSavedSingles } = await import('../src/processing/singleWorkflow')
  const { restoreSavedBatches } = await import('../src/components/BatchProcessor')
  restoreSavedSingles(new Set(['b1']))
  restoreSavedBatches(new Set(['b1']))
  expect(api.fetchBook).not.toHaveBeenCalled()
  expect(api.fetchJob).not.toHaveBeenCalled()
  expect(api.createJob).not.toHaveBeenCalled()
  expect(localStorage.getItem('ndr:tasks:v1:single:other-library')).toBe('invalid legacy data')
  expect(localStorage.getItem('ndr:tasks:v1:batch:deleted-book')).toBe('invalid legacy data')
})
it('restores single in-flight IDs, dispatches only pending windows with saved keys and marks completion', async () => {
  const { runSingleWorkflow } = await import('../src/processing/singleWorkflow')
  await runSingleWorkflow(single())
  expect(api.fetchJob).toHaveBeenCalledWith('old')
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ idempotencyKey: 'next-key' }))
  expect(api.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1')
  expect(JSON.parse(localStorage.getItem('ndr:tasks:v1:single:b1')!).running).toBe(false)
})
it('restores a lost acknowledgement using the original key, never a new paid request key', async () => {
  const work = single(); work.tasks[0].job = null
  localStorage.setItem('ndr:tasks:v1:single:b1', JSON.stringify(work))
  const { restoreSavedSingles } = await import('../src/processing/singleWorkflow')
  restoreSavedSingles(new Set(['b1']))
  await vi.waitFor(() => expect(JSON.parse(localStorage.getItem('ndr:tasks:v1:single:b1')!).running).toBe(false))
  expect(api.createJob.mock.calls.map(([input]) => input.idempotencyKey)).toEqual(['old-key', 'next-key'])
})
it('does not dispatch old single jobs after a book version change', async () => {
  api.fetchBook.mockResolvedValue({ active_version_id: 'v2' })
  const { runSingleWorkflow } = await import('../src/processing/singleWorkflow')
  await expect(runSingleWorkflow(single())).rejects.toThrow('版本已改变')
  expect(api.createJob).not.toHaveBeenCalled()
})
async function seedBatch(stopRequested = false) {
  const { getProcessingPreferences } = await import('../src/processing/preferences')
  const chapter = { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 100, dialogue_processed: false }
  const estimate = { total_tokens: 20, windows: [
    { window_id: 'w1', ordinal: 1, target_count: 1, estimated_tokens: 10 },
    { window_id: 'w2', ordinal: 2, target_count: 1, estimated_tokens: 10 },
  ] }
  const record = { schema: 1, execution: { bookId: 'b1', bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }],
    preferences: { ...getProcessingPreferences(), profileId: 'p1', concurrency: 1, tokenLimit: null as number | null } },
    snapshot: { running: true, startedAt: 1, finishedAt: null, stopRequested, message: '', revision: 1,
      chapterStates: { c1: { state: 'dialogue', completedWindows: 0, totalWindows: 2, error: null } },
      annotationRevisions: {}, catalogRevision: 0,
      tasks: [{ id: 'roster:c1', type: 'roster', chapterId: 'c1', state: 'completed', jobId: 'r', error: null },
        ...['w1', 'w2'].map((id, i) => ({ id: `dialogue:c1:${id}`, type: 'dialogue', chapterId: 'c1', windowId: id,
          state: i === 0 ? 'running' : 'queued', error: null }))] },
    requests: { 'roster:c1': { type: 'roster', jobId: 'r', input: { idempotencyKey: 'roster-key' } },
      'dialogue:c1:w1': { type: 'dialogue', jobId: 'old', input: { ...single().tasks[0].input, idempotencyKey: 'old-key' } } },
    accounted: ['r'], spent: 20, totalSpent: 30, unknownRuns: 0 }
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(record))
  return record
}
it('restores a batch, keeps cumulative usage and does not repeat roster or acknowledged windows', async () => {
  await seedBatch()
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  expect(api.analyzeRoster).not.toHaveBeenCalled()
  expect(api.confirmRoster).not.toHaveBeenCalled()
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ selectedWindowIds: ['w2'] }))
  const saved = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(saved.spent).toBe(40); expect(saved.totalSpent).toBe(50)
  expect(saved.snapshot.running).toBe(false)
})
it('reconciles stale running rows even when the saved batch has already ended', async () => {
  const saved = await seedBatch()
  saved.snapshot.running = false
  saved.snapshot.tasks = saved.snapshot.tasks.filter(task => task.id !== 'dialogue:c1:w2')
  api.fetchChapters.mockResolvedValue([{ ...saved.execution.requested[0], dialogue_processed: true }])
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(saved))
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  const restored = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(restored.snapshot.chapterStates.c1.state).toBe('processed')
  expect(restored.snapshot.tasks.every((task: { state: string }) => task.state === 'completed')).toBe(true)
  expect(restored.snapshot.running).toBe(false)
  expect(api.createJob).not.toHaveBeenCalled()
})
it('clears stale chapter activity when recovery stops on unknown usage', async () => {
  const saved = await seedBatch()
  saved.execution.preferences.tokenLimit = 100
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(saved))
  api.fetchJob.mockResolvedValue({ ...completed('old'), unknown_usage_runs: 1 })
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  const restored = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(restored.snapshot.chapterStates.c1.state).toBe('stopped')
  expect(restored.snapshot.tasks.find((task: { id: string }) => task.id === 'dialogue:c1:w1').state).toBe('completed')
  expect(api.createJob).not.toHaveBeenCalled()
})
it('a saved stop prevents any queue dispatch after reload', async () => {
  await seedBatch(true)
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  expect(api.createJob).not.toHaveBeenCalled(); expect(api.analyzeRoster).not.toHaveBeenCalled()
  const saved = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(saved.snapshot.running).toBe(false)
})
it('restores a cancelled chapter without stale running tasks or new dispatch', async () => {
  const saved = await seedBatch()
  Object.assign(saved.snapshot.chapterStates.c1, { state: 'stopped', cancelRequested: true })
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(saved))
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  expect(api.createJob).not.toHaveBeenCalled()
  const restored = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(restored.snapshot.chapterStates.c1.cancelRequested).toBe(false)
  expect(restored.snapshot.tasks.some((task: { state: string }) => ['queued', 'running'].includes(task.state))).toBe(false)
  expect(restored.snapshot.message).toContain('已取消 1 章')
})
it('recovers a batch acknowledgement through a read-only request-key lookup', async () => {
  const saved = await seedBatch()
  delete (saved.requests['dialogue:c1:w1'] as { jobId?: string }).jobId
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(saved))
  api.fetchRecentJobs.mockResolvedValue([completed('old')])
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  expect(api.fetchRecentJobs).toHaveBeenCalledWith(expect.objectContaining({ idempotencyKey: 'old-key' }))
  expect(api.createJob).toHaveBeenCalledTimes(1)
})

it('waits for old provider slots and honours a stop received during restoration', async () => {
  await seedBatch()
  api.fetchJob.mockImplementation(async id => id === 'old' ? { ...completed(id), state: 'RUNNING' } : completed(id))
  let release!: () => void
  api.waitJob.mockImplementation(async (job, update) => {
    if (job.id === 'old') await new Promise<void>(resolve => { release = resolve })
    const done = completed(job.id); update(done); return done
  })
  const { restoreBatchProcessing, requestBatchStop } = await import('../src/components/BatchProcessor')
  const restore = restoreBatchProcessing('b1')
  await vi.waitFor(() => expect(release).toBeDefined())
  expect(api.createJob).not.toHaveBeenCalled()
  requestBatchStop('b1')
  release(); await restore
  expect(api.createJob).not.toHaveBeenCalled()
  expect(JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!).snapshot.running).toBe(false)
})

it('preserves unknown usage and stops a limited queue instead of dispatching again', async () => {
  const saved = await seedBatch()
  saved.execution.preferences.tokenLimit = 100
  localStorage.setItem('ndr:tasks:v1:batch:b1', JSON.stringify(saved))
  api.fetchJob.mockImplementation(async id => ({ ...completed(id), unknown_usage_runs: id === 'old' ? 1 : 0 }))
  const { restoreBatchProcessing } = await import('../src/components/BatchProcessor')
  await restoreBatchProcessing('b1')
  expect(api.createJob).not.toHaveBeenCalled()
  const restored = JSON.parse(localStorage.getItem('ndr:tasks:v1:batch:b1')!)
  expect(restored.unknownRuns).toBe(1)
  expect(restored.snapshot.message).toContain('用量未知')
})

it('serializes two tab schedulers for the same book', async () => {
  const { withWorkflowLock } = await import('../src/processing/journal')
  const order: string[] = []
  let release!: () => void
  const first = withWorkflowLock('processing:b1', async () => {
    order.push('first'); await new Promise<void>(resolve => { release = resolve }); order.push('finished')
  })
  await vi.waitFor(() => expect(release).toBeDefined())
  const second = withWorkflowLock('processing:b1', async () => { order.push('second') })
  expect(order).toEqual(['first'])
  release(); await Promise.all([first, second])
  expect(order).toEqual(['first', 'finished', 'second'])
})
