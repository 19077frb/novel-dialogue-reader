import { beforeEach, expect, it, vi } from 'vitest'
import type { SingleWorkflow } from '../src/processing/singleWorkflow'
import type { JobDetailOut } from '../src/api/types'

const api = vi.hoisted(() => ({ fetchBook: vi.fn(), fetchJob: vi.fn(), createJob: vi.fn(), pauseJob: vi.fn(),
  fetchRecentJobs: vi.fn(), completeChapterProcessing: vi.fn(), setChapterProcessingStatus: vi.fn(), waitJob: vi.fn() }))
vi.mock('../src/api/books', () => api)
vi.mock('../src/api/jobs', () => api)
vi.mock('../src/processing/jobCompletion', () => ({ waitForJobCompletion: (job: unknown, update: unknown) => api.waitJob(job, update) }))
const job = (id: string, state: JobDetailOut['state'] = 'COMPLETED') => ({ id, state } as JobDetailOut)
function work(queueId?: string): SingleWorkflow {
  return { bookId: 'fake-book', versionId: 'fake-version', chapterId: 'fake-chapter', queueId, concurrency: 1,
    startCp: 0, endCp: 100, mode: 'process', running: true, completeChapter: true, error: null,
    tasks: ['first', 'second'].map(windowId => ({ windowId, ordinal: windowId, job: null, error: null,
      input: { bookId: 'fake-book', bookVersionId: 'fake-version', mode: 'process',
        range: { chapterId: 'fake-chapter', startCp: 0, endCp: 100 }, selectedWindowIds: [windowId],
        profileId: 'fake-profile', readingMode: 'initial', visibleHorizonCp: 100,
        budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 0 }, idempotencyKey: `${queueId ?? 'legacy'}:${windowId}` } })) }
}
const saved = () => JSON.parse(localStorage.getItem('ndr:tasks:v1:single:fake-book')!) as SingleWorkflow
function storage(key = 'ndr:tasks:v1:single-stop:fake-book') { window.dispatchEvent(new StorageEvent('storage', { key })) }
async function otherTab() { vi.resetModules(); return import('../src/processing/singleWorkflow') }
beforeEach(() => {
  vi.resetModules(); vi.resetAllMocks(); localStorage.clear()
  const locks = new Map<string, Promise<unknown>>()
  Object.defineProperty(navigator, 'locks', { configurable: true, value: {
    request: (key: string, action: () => Promise<unknown>) => {
      const pending = (locks.get(key) ?? Promise.resolve()).then(action)
      locks.set(key, pending.catch(() => undefined)); return pending
    },
  } })
  api.fetchBook.mockResolvedValue({ active_version_id: 'fake-version' })
  api.fetchRecentJobs.mockResolvedValue([])
  api.fetchJob.mockImplementation(async id => job(id))
  api.createJob.mockImplementation(async input => job(input.idempotencyKey))
  api.pauseJob.mockImplementation(async id => job(id, 'PAUSED'))
  api.waitJob.mockImplementation(async (initial, update) => { update(initial); return initial })
})

it('a spectator stop persists a scoped command without overwriting the owner, then pauses a late acknowledgement', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  let acknowledge!: (value: JobDetailOut) => void
  api.createJob.mockImplementationOnce(() => new Promise(resolve => { acknowledge = resolve }))
  const execution = owner.runSingleWorkflow(work('range-one'))
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => api.createJob.mock.calls.length === 1)
  const spectator = await otherTab()
  const beforeStop = localStorage.getItem('ndr:tasks:v1:single:fake-book')
  await spectator.stopSingleWorkflow('fake-book')
  expect(localStorage.getItem('ndr:tasks:v1:single:fake-book')).toBe(beforeStop)
  storage()
  expect(saved().stopRequested).toBe(true)
  acknowledge(job('late-accepted', 'QUEUED'))
  await ended
  expect(api.pauseJob).toHaveBeenCalledWith('late-accepted')
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(saved().tasks[0].job?.id).toBe('late-accepted')
  expect(saved().tasks[1].error).toContain('未派发')
  expect(saved().running).toBe(false)
  expect(api.completeChapterProcessing).not.toHaveBeenCalled()
})

it('reads an admission stop after slot acquisition and preserves completed results', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  let finish!: () => void
  api.waitJob.mockImplementationOnce(async (initial, update) => {
    update(initial)
    await new Promise<void>(resolve => { finish = resolve })
    update(job(initial.id)); return job(initial.id)
  })
  const execution = owner.runSingleWorkflow(work('range-one'))
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => Boolean(finish))
  localStorage.setItem('ndr:tasks:v1:admissions', JSON.stringify([{ id: 'range-one', stopRequested: true }]))
  // No storage event: dispatch-boundary reads remain the safety net.
  finish(); await ended
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(saved().tasks[0].job?.state).toBe('COMPLETED')
  expect(api.completeChapterProcessing).not.toHaveBeenCalled()
})

it('pauses known running jobs in the owner and preserves pause failure diagnostics until actual completion', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  let finish!: () => void
  api.createJob.mockResolvedValueOnce(job('running', 'RUNNING'))
  api.pauseJob.mockRejectedValue(new Error('fake pause failure'))
  api.waitJob.mockImplementationOnce(async (initial, update) => {
    update(initial); await new Promise<void>(resolve => { finish = resolve })
    update(job(initial.id)); return job(initial.id)
  })
  const execution = owner.runSingleWorkflow(work('range-one'))
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => Boolean(finish))
  localStorage.setItem('ndr:tasks:v1:admissions', JSON.stringify([{ id: 'range-one', stopRequested: true }]))
  storage('ndr:tasks:v1:admissions')
  await vi.waitUntil(() => saved().tasks[0].error?.includes('fake pause failure') === true)
  expect(saved().running).toBe(true)
  finish(); await ended
  expect(saved().tasks[0].job?.state).toBe('COMPLETED')
  expect(saved().tasks[0].error).toContain('fake pause failure')
  expect(api.createJob).toHaveBeenCalledTimes(1)
})

it('restores a legacy stop by reads only and does not stop a later admission', async () => {
  const legacy = work()
  localStorage.setItem('ndr:tasks:v1:single:fake-book', JSON.stringify(legacy))
  const spectator = await import('../src/processing/singleWorkflow')
  await spectator.stopSingleWorkflow('fake-book')
  const refreshed = await otherTab()
  await expect(refreshed.runSingleWorkflow(legacy)).rejects.toThrow('安全停止')
  expect(api.createJob).not.toHaveBeenCalled()
  expect(api.fetchRecentJobs).toHaveBeenCalledTimes(2)
  await refreshed.runSingleWorkflow(work('new-range'))
  expect(api.createJob).toHaveBeenCalledTimes(2)
  expect(api.createJob).toHaveBeenCalledWith(expect.objectContaining({ readingMode: 'initial', idempotencyKey: 'new-range:first' }))
  expect(saved().stopRequested).toBe(false)
})

it('a second legacy owner rechecks the finished journal after waiting for the processing lock', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  let finish!: () => void
  api.waitJob.mockImplementationOnce(async (initial, update) => {
    update(initial); await new Promise<void>(resolve => { finish = resolve }); return initial
  })
  const execution = owner.runSingleWorkflow(work())
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => Boolean(finish))
  const spectator = await otherTab()
  const staleWork = saved()
  const waiting = spectator.runSingleWorkflow(staleWork)
  const waitingEnded = expect(waiting).rejects.toThrow('安全停止')
  await spectator.stopSingleWorkflow('fake-book')
  storage(); finish()
  await Promise.all([ended, waitingEnded])
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(api.fetchRecentJobs).toHaveBeenCalledTimes(1)
})

it('stops dispatch on a damaged stop record and retains a clear journal error', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  let finish!: () => void
  api.waitJob.mockImplementationOnce(async (initial, update) => {
    update(initial); await new Promise<void>(resolve => { finish = resolve }); update(initial); return initial
  })
  const execution = owner.runSingleWorkflow(work('range-one'))
  const ended = expect(execution).rejects.toThrow('保存的任务记录无法读取')
  await vi.waitUntil(() => Boolean(finish))
  localStorage.setItem('ndr:tasks:v1:single-stop:fake-book', '{broken')
  storage(); finish(); await ended
  expect(api.createJob).toHaveBeenCalledTimes(1)
  expect(saved().error).toContain('保存的任务记录无法读取')
  expect(saved().running).toBe(false)
})

it('syncs spectator snapshots from storage without running a second scheduler', async () => {
  const { renderHook, act, cleanup } = await import('@testing-library/react/pure')
  const spectator = await import('../src/processing/singleWorkflow')
  const completed = { ...work('range-one'), running: false }
  localStorage.setItem('ndr:tasks:v1:single:fake-book', JSON.stringify(completed))
  const rendered = renderHook(() => spectator.useSingleWorkflow('fake-book'))
  try {
    expect(rendered.result.current?.running).toBe(false)
    act(() => {
      localStorage.setItem('ndr:tasks:v1:single:fake-book', JSON.stringify({ ...completed, error: '已安全停止', stopRequested: true }))
      storage('ndr:tasks:v1:single:fake-book')
    })
    expect(rendered.result.current?.error).toBe('已安全停止')
    expect(spectator.hasSingleWork()).toBe(false)
    expect(api.fetchBook).not.toHaveBeenCalled()
    expect(api.createJob).not.toHaveBeenCalled()
  } finally { cleanup() }
})


it('does not post when a cross-tab stop arrives while waiting for the shared slot', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  const { inSharedTaskPool } = await import('../src/processing/concurrency')
  let release!: () => void
  const held = inSharedTaskPool(1, () => new Promise<void>(resolve => { release = resolve }))
  await vi.waitUntil(() => Boolean(release))
  const execution = owner.runSingleWorkflow(work('range-one'))
  const ended = expect(execution).rejects.toThrow('安全停止')
  await vi.waitUntil(() => api.fetchBook.mock.calls.length === 1)
  const spectator = await otherTab()
  await spectator.stopSingleWorkflow('fake-book')
  storage(); release(); await held; await ended
  expect(api.createJob).not.toHaveBeenCalled()
  expect(api.completeChapterProcessing).not.toHaveBeenCalled()
  expect(saved().tasks.every(task => task.error?.includes('未派发'))).toBe(true)
})

it('detects a stop from polling updates even when no storage event arrives', async () => {
  const owner = await import('../src/processing/singleWorkflow')
  api.createJob.mockResolvedValueOnce(job('running', 'RUNNING'))
  api.waitJob.mockImplementationOnce(async (initial, update) => {
    update(initial)
    localStorage.setItem('ndr:tasks:v1:admissions', JSON.stringify([{ id: 'range-one', stopRequested: true }]))
    update(job(initial.id, 'RUNNING'))
    update(job(initial.id)); return job(initial.id)
  })
  await expect(owner.runSingleWorkflow(work('range-one'))).rejects.toThrow('安全停止')
  expect(api.pauseJob).toHaveBeenCalledTimes(1)
  expect(api.pauseJob).toHaveBeenCalledWith('running')
  expect(saved().tasks[0].job?.state).toBe('COMPLETED')
  expect(api.createJob).toHaveBeenCalledTimes(1)
})
