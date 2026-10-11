import { beforeEach, expect, it, vi } from 'vitest'
import { waitFor } from '@testing-library/react'
import { enqueueWork, getAdmissions, cancelAdmission, pumpQueue, stopAdmission } from '../src/processing/workQueue'
import type { SingleWorkflow } from '../src/processing/singleWorkflow'
import { runSingleWorkflow, stopSingleWorkflow } from '../src/processing/singleWorkflow'
import { fetchBook } from '../src/api/books'
import { writeJournal } from '../src/processing/journal'
import { inSharedTaskPool } from '../src/processing/concurrency'
import { fetchTaskQueue } from '../src/api/jobs'
import { startCharacterAutoMerge } from '../src/api/characters'
import { waitForJobCompletion } from '../src/processing/jobCompletion'
import { fetchJob } from '../src/api/books'

vi.mock('../src/api/books', () => ({ fetchBook: vi.fn(), fetchJob: vi.fn() }))
vi.mock('../src/api/jobs', () => ({ fetchTaskQueue: vi.fn(), pauseJob: vi.fn() }))
vi.mock('../src/api/characters', () => ({ startCharacterAutoMerge: vi.fn() }))
vi.mock('../src/processing/jobCompletion', () => ({ waitForJobCompletion: vi.fn() }))
vi.mock('../src/processing/singleWorkflow', () => ({ runSingleWorkflow: vi.fn(), stopSingleWorkflow: vi.fn() }))

const work = (bookId: string, windowId: string): SingleWorkflow => ({
  bookId, versionId: 'v1', chapterId: 'c1', startCp: 0, endCp: 100, mode: 'process', concurrency: 2,
  completeChapter: false, running: true, error: null,
  tasks: [{ windowId, ordinal: '1', job: null, error: null, input: {
    bookId, mode: 'process', bookVersionId: 'v1', range: { chapterId: 'c1', startCp: 0, endCp: 100 },
    selectedWindowIds: [windowId], profileId: 'p1', readingMode: 'reread',
    budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 0 },
    idempotencyKey: `${bookId}:${windowId}:original`,
  } }],
})
const input = (bookId: string, windowId: string) => ({ bookId, versionId: 'v1', title: windowId,
  keys: [`dialogue:c1:${windowId}`], payload: { type: 'single' as const, work: work(bookId, windowId) } })

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  Object.defineProperty(navigator, 'locks', { configurable: true,
    value: { request: async (_key: string, action: () => Promise<unknown>) => action() } })
  vi.mocked(fetchBook).mockImplementation(async id => ({ id, title: id, active_version_id: 'v1' } as never))
  vi.mocked(runSingleWorkflow).mockResolvedValue({ id: 'job', state: 'COMPLETED' } as never)
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(startCharacterAutoMerge).mockResolvedValue({ id: 'merge-job', state: 'QUEUED' } as never)
  vi.mocked(waitForJobCompletion).mockResolvedValue({ id: 'merge-job', state: 'COMPLETED' } as never)
})

const mergeInput = { bookId: 'merge-book', versionId: 'v1', title: '自动合并', keys: ['merge'],
  payload: { type: 'merge' as const, input: { book_version_id: 'v1', profile_id: 'p1',
    idempotency_key: 'merge-key', run_now: true } } }

it('explains blocked merge tasks without holding a model slot and cancels without dispatch', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [{ id: 'old-roster', state: 'QUEUED',
    chapter_title: '第十二卷章节' }], next_cursor: null } as never)
  const id = await enqueueWork(mergeInput)
  await waitFor(() => expect(getAdmissions().find(item => item.id === id)?.waiting?.jobId).toBe('old-roster'))
  expect(getAdmissions().find(item => item.id === id)?.waiting?.reason).toContain('继续或暂停')
  let entered = false
  await inSharedTaskPool(1, async () => { entered = true })
  expect(entered).toBe(true)
  await stopAdmission(id)
  await waitFor(() => expect(getAdmissions().find(item => item.id === id)?.phase).toBe('cancelled'), { timeout: 2500 })
  expect(startCharacterAutoMerge).not.toHaveBeenCalled()
})

it('dispatches merge once after the blocker ends and clears its waiting summary', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValueOnce({ items: [{ id: 'active', state: 'RUNNING' }], next_cursor: null } as never)
  const id = await enqueueWork(mergeInput)
  await waitFor(() => expect(getAdmissions().find(item => item.id === id)?.phase).toBe('completed'), { timeout: 2500 })
  expect(startCharacterAutoMerge).toHaveBeenCalledTimes(1)
  expect(getAdmissions().find(item => item.id === id)?.waiting).toBeUndefined()
})

it('restores an admitted merge by jobId rather than creating another request', async () => {
  vi.mocked(fetchJob).mockResolvedValue({ id: 'saved-job', state: 'COMPLETED' } as never)
  writeJournal('admissions', [{ ...mergeInput, id: 'saved-merge', jobId: 'saved-job',
    phase: 'running', error: null, createdAt: 1 }])
  await pumpQueue()
  await waitFor(() => expect(getAdmissions()[0].phase).toBe('completed'))
  expect(fetchJob).toHaveBeenCalledWith('saved-job')
  expect(startCharacterAutoMerge).not.toHaveBeenCalled()
  expect(fetchTaskQueue).not.toHaveBeenCalled()
})

it('keeps frozen options, serializes a book and accepts other books and windows', async () => {
  let finish!: (value: never) => void
  vi.mocked(runSingleWorkflow).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const firstInput = input('b1', 'w1')
  const first = await enqueueWork(firstInput)
  await waitFor(() => expect(runSingleWorkflow).toHaveBeenCalledTimes(1))
  firstInput.payload.work.tasks[0].input.profileId = 'changed'
  await expect(enqueueWork(input('b1', 'w1'))).rejects.toThrow('该窗口已有任务')
  const next = await enqueueWork(input('b1', 'w2'))
  await enqueueWork(input('b2', 'w1'))
  await waitFor(() => expect(runSingleWorkflow).toHaveBeenCalledTimes(2))
  expect(getAdmissions().find(item => item.id === next)?.phase).toBe('queued')
  const frozen = getAdmissions().find(item => item.id === first)!
  expect(frozen.payload.type === 'single' && frozen.payload.work.tasks[0].input.profileId).toBe('p1')
  finish({ id: 'j1', state: 'COMPLETED' } as never)
  await waitFor(() => expect(getAdmissions().every(item => item.phase === 'completed')).toBe(true))
  expect(runSingleWorkflow).toHaveBeenCalledTimes(3)
})

it('cancels only a queued range and never removes active work during pruning', async () => {
  let finish!: (value: never) => void
  vi.mocked(runSingleWorkflow).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  await enqueueWork(input('b1', 'w1'))
  await waitFor(() => expect(runSingleWorkflow).toHaveBeenCalledTimes(1))
  const queued = await enqueueWork(input('b1', 'w2'))
  await cancelAdmission(queued)
  expect(getAdmissions().find(item => item.id === queued)?.phase).toBe('cancelled')
  finish({ id: 'j1', state: 'COMPLETED' } as never)
  await waitFor(() => expect(getAdmissions()[0].phase).toBe('completed'))
  expect(runSingleWorkflow).toHaveBeenCalledTimes(1)
})

it('restores saved requests and refuses deleted books without model calls', async () => {
  const saved = { ...input('b1', 'w1'), id: 'saved', phase: 'queued', error: null, createdAt: 1 }
  writeJournal('admissions', [saved])
  await pumpQueue(new Set(['b1']))
  await waitFor(() => expect(getAdmissions()[0].phase).toBe('completed'))
  expect(vi.mocked(runSingleWorkflow).mock.calls[0][0].tasks[0].input.idempotencyKey).toBe('b1:w1:original')
  writeJournal('admissions', [{ ...saved, id: 'missing', bookId: 'deleted' }])
  await pumpQueue(new Set(['b1']))
  expect(getAdmissions()[0].phase).toBe('failed')
  expect(runSingleWorkflow).toHaveBeenCalledTimes(1)
})

it('does not stop an unrelated legacy workflow when a new admission waits for it', async () => {
  let finish!: (value: never) => void
  vi.mocked(runSingleWorkflow).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const id = await enqueueWork(input('b1', 'w1'))
  await waitFor(() => expect(runSingleWorkflow).toHaveBeenCalledTimes(1))
  writeJournal('single:b1', { ...work('b1', 'legacy'), queueId: 'old-range' })
  await stopAdmission(id)
  expect(stopSingleWorkflow).not.toHaveBeenCalled()
  finish({ id: 'j1', state: 'COMPLETED' } as never)
  await waitFor(() => expect(getAdmissions()[0].phase).toBe('cancelled'))
})

it('shares slots across workflows and holds them until real completion', async () => {
  let active = 0; let maximum = 0
  const finishers: (() => void)[] = []
  const tasks = Array.from({ length: 4 }, () => inSharedTaskPool(2, async () => {
    active++; maximum = Math.max(maximum, active)
    await new Promise<void>(resolve => finishers.push(resolve))
    active--
  }))
  await waitFor(() => expect(finishers.length).toBe(2))
  finishers[0](); finishers[1]()
  await waitFor(() => expect(finishers.length).toBe(4))
  finishers[2](); finishers[3]()
  await Promise.all(tasks)
  expect(maximum).toBe(2)
})

it('releases completed windows rather than blocking them until the whole range finishes', async () => {
  let finish!: (value: never) => void
  vi.mocked(runSingleWorkflow).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
  const id = await enqueueWork(input('b1', 'w1'))
  await waitFor(() => expect(runSingleWorkflow).toHaveBeenCalledTimes(1))
  const saved = work('b1', 'w1')
  saved.queueId = id
  saved.tasks[0].job = { id: 'done', state: 'COMPLETED' } as never
  writeJournal('single:b1', saved)
  await expect(enqueueWork(input('b1', 'w1'))).resolves.toBeTypeOf('string')
  finish({ id: 'done', state: 'COMPLETED' } as never)
  await waitFor(() => expect(getAdmissions().every(item => item.phase === 'completed')).toBe(true))
})
