import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import * as books from '../src/api/books'
import * as characters from '../src/api/characters'
import * as jobs from '../src/api/jobs'
import { ApiError } from '../src/api/client'
import { clearBatchProgress, requestBatchStop, restoreBatchProcessing, runBatchProcessing } from '../src/components/BatchProcessor'
import { getProcessingPreferences } from '../src/processing/preferences'
import { readJournal, writeJournal } from '../src/processing/journal'
import { waitForJobCompletion } from '../src/processing/jobCompletion'
import type { ChapterOut, EstimateOut, JobDetailOut } from '../src/api/types'

vi.mock('../src/api/books', () => ({ fetchJob: vi.fn(), completeChapterProcessing: vi.fn(), fetchBook: vi.fn(), fetchChapters: vi.fn(), fetchProcessingStatus: vi.fn(), setChapterProcessingStatus: vi.fn() }))
vi.mock('../src/api/characters', () => ({ analyzeCharacterRoster: vi.fn(), confirmCharacterRoster: vi.fn(), fetchCharacterRoster: vi.fn() }))
vi.mock('../src/api/jobs', async importOriginal => ({ ...await importOriginal<typeof import('../src/api/jobs')>(), preflightDialogue: vi.fn(), fetchRecentJobs: vi.fn(), pauseJob: vi.fn(), createJob: vi.fn(), estimateRange: vi.fn() }))
vi.mock('../src/processing/jobCompletion', async importOriginal => ({ ...await importOriginal<typeof import('../src/processing/jobCompletion')>(), waitForJobCompletion: vi.fn(async job => job) }))

const bookId = 'preflight-original-book'
const chapter = { id: 'chapter-one', title: '原创第一章', ordinal: 0, start_cp: 0, end_cp: 100, dialogue_processed: false } as ChapterOut
const estimate = { book_version_id: 'v1', windows: [
  { window_id: 'window-one', ordinal: 1, target_count: 1, estimated_tokens: 10 },
] } as unknown as EstimateOut
const preferences = () => ({ ...getProcessingPreferences(), profileId: 'preflight-profile', concurrency: 1, tokenLimit: null })
const completedJob = { id: 'original-roster-job', kind: 'CHARACTER_ROSTER', state: 'COMPLETED',
  usage: {}, unknown_usage_runs: 0 } as JobDetailOut
const roster = { status: 'CONFIRMED', version: 1, candidates: [{ canonical_name: '原创人物', temp_ref: 'person-one', aliases: [] }] }

beforeEach(() => {
  localStorage.clear()
  vi.resetAllMocks()
  clearBatchProgress(bookId)
  vi.mocked(jobs.preflightDialogue).mockResolvedValue(undefined)
  vi.mocked(jobs.fetchRecentJobs).mockResolvedValue([])
  vi.mocked(books.fetchBook).mockResolvedValue({ active_version_id: 'v1' } as never)
  vi.mocked(books.fetchChapters).mockResolvedValue([chapter])
  vi.mocked(books.fetchJob).mockResolvedValue(completedJob)
  vi.mocked(characters.fetchCharacterRoster).mockResolvedValue(roster as never)
  vi.mocked(characters.analyzeCharacterRoster).mockResolvedValue(completedJob)
  vi.mocked(characters.confirmCharacterRoster).mockResolvedValue(roster as never)
  vi.mocked(jobs.createJob).mockResolvedValue({ ...completedJob, id: 'dialogue-job', kind: 'INFERENCE' })
  vi.mocked(waitForJobCompletion).mockImplementation(async job => job)
})

afterEach(() => { clearBatchProgress(bookId); localStorage.clear() })

function start(reuseRoster = false) {
  return runBatchProcessing({ bookId, bookVersionId: 'v1', requested: [chapter],
    plans: [{ chapter, estimate, reuseRoster }], preferences: preferences() })
}

function savePendingRoster(jobId?: string) {
  writeJournal(`batch:${bookId}`, { schema: 1,
    requests: { 'roster:chapter-one': { type: 'roster', input: {
      bookVersionId: 'v1', profileId: 'preflight-profile', idempotencyKey: 'saved-roster-key',
    }, ...(jobId ? { jobId } : {}) } },
    accounted: [], spent: 0, totalSpent: 0, unknownRuns: 0,
    snapshot: { running: true, startedAt: 1, stopRequested: false, message: '',
      tasks: [{ id: 'roster:chapter-one', type: 'roster', chapterId: chapter.id, state: 'queued' },
        { id: 'dialogue:chapter-one:window-one', type: 'dialogue', chapterId: chapter.id, windowId: 'window-one', state: 'queued' }],
      chapterStates: {}, annotationRevisions: {}, catalogRevision: 0, revision: 0 },
    execution: { bookId, bookVersionId: 'v1', requested: [chapter], plans: [{ chapter, estimate }], preferences: preferences() },
  })
}

it('checks the exact planned dialogue scope before spending on people', async () => {
  await start()
  expect(jobs.preflightDialogue).toHaveBeenCalledWith({ bookId, bookVersionId: 'v1',
    range: { chapterId: chapter.id, startCp: 0, endCp: 100, dialogueStrategy: preferences().dialogueStrategy },
    selectedWindowIds: ['window-one'], readingMode: 'reread', visibleHorizonCp: null })
  expect(vi.mocked(jobs.preflightDialogue).mock.invocationCallOrder[0]).toBeLessThan(
    vi.mocked(characters.analyzeCharacterRoster).mock.invocationCallOrder[0])
  expect(characters.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
})

it.each([
  new ApiError(409, { code: 'RESOURCE_CONFLICT', message: '目标对白的原任务待核对', details: { job_id: 'uncertain-job' } }),
  new Error('预检查读取失败'),
])('keeps preflight failure visible and dispatches no paid work', async error => {
  vi.mocked(jobs.preflightDialogue).mockRejectedValue(error)
  await start()
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(characters.confirmCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).not.toHaveBeenCalled()
  expect(readJournal<{ snapshot: { tasks: { state: string; error: string }[] } }>(`batch:${bookId}`)?.snapshot.tasks)
    .toContainEqual(expect.objectContaining({ state: 'failed', error: error.message }))
})

it('uses a confirmed roster without an extra people analysis or confirmation', async () => {
  await start(true)
  expect(jobs.preflightDialogue).not.toHaveBeenCalled()
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(characters.confirmCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
})

it('honors a whole-range stop while the read-only preflight is pending', async () => {
  let finishPreflight!: () => void
  vi.mocked(jobs.preflightDialogue).mockImplementation(() => new Promise(resolve => { finishPreflight = resolve }))
  const running = start()
  await vi.waitFor(() => expect(jobs.preflightDialogue).toHaveBeenCalledTimes(1))
  requestBatchStop(bookId)
  finishPreflight()
  await running
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).not.toHaveBeenCalled()
})

it.each([true, false])('restores an existing people receipt before considering a new admission (%s)', async acknowledged => {
  savePendingRoster(acknowledged ? completedJob.id : undefined)
  vi.mocked(jobs.fetchRecentJobs).mockResolvedValue([completedJob])
  vi.mocked(jobs.preflightDialogue).mockRejectedValue(new Error('已有对白任务占用'))
  await restoreBatchProcessing(bookId)
  expect(jobs.preflightDialogue).not.toHaveBeenCalled()
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(characters.confirmCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
  if (acknowledged) expect(books.fetchJob).toHaveBeenCalledWith(completedJob.id)
  else expect(jobs.fetchRecentJobs).toHaveBeenCalledWith(expect.objectContaining({ idempotencyKey: 'saved-roster-key' }))
})

it('checks a persisted but never admitted roster request before sending it', async () => {
  savePendingRoster()
  vi.mocked(jobs.preflightDialogue).mockRejectedValue(new ApiError(409, {
    code: 'RESOURCE_CONFLICT', message: '原对白结果待核对',
  }))
  await restoreBatchProcessing(bookId)
  expect(jobs.fetchRecentJobs).toHaveBeenCalledWith(expect.objectContaining({ idempotencyKey: 'saved-roster-key' }))
  expect(jobs.preflightDialogue).toHaveBeenCalledTimes(1)
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).not.toHaveBeenCalled()
})

it('recovers a late people receipt before the new-admission preflight', async () => {
  savePendingRoster()
  vi.mocked(jobs.fetchRecentJobs).mockResolvedValueOnce([]).mockResolvedValue([completedJob])
  vi.mocked(jobs.preflightDialogue).mockRejectedValue(new Error('已有对白占用'))
  await restoreBatchProcessing(bookId)
  expect(jobs.fetchRecentJobs).toHaveBeenCalledTimes(2)
  expect(jobs.preflightDialogue).not.toHaveBeenCalled()
  expect(characters.analyzeCharacterRoster).not.toHaveBeenCalled()
  expect(characters.confirmCharacterRoster).not.toHaveBeenCalled()
  expect(jobs.createJob).toHaveBeenCalledTimes(1)
})
