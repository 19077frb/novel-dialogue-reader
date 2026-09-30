import { afterEach, describe, expect, it, vi } from 'vitest'

import { fetchJob } from '../src/api/books'
import { ApiError } from '../src/api/client'
import type { JobDetailOut } from '../src/api/types'
import { waitForJobCompletion } from '../src/processing/jobCompletion'

vi.mock('../src/api/books', () => ({ fetchJob: vi.fn() }))
const job = (state: JobDetailOut['state']) => ({ id: 'j1', state } as JobDetailOut)

afterEach(() => {
  vi.useRealTimers()
  vi.mocked(fetchJob).mockReset()
})

describe('waitForJobCompletion', () => {
  it('QUEUED 和 RUNNING 都不是结束，持续只读跟踪到 COMPLETED', async () => {
    vi.useFakeTimers()
    vi.mocked(fetchJob).mockResolvedValueOnce(job('RUNNING')).mockResolvedValueOnce(job('COMPLETED'))
    const onUpdate = vi.fn()
    const pending = waitForJobCompletion(job('QUEUED'), onUpdate)
    expect(onUpdate).toHaveBeenCalledWith(job('QUEUED'))
    await vi.runAllTimersAsync()
    expect(await pending).toEqual(job('COMPLETED'))
    expect(onUpdate.mock.calls.map(([snapshot]) => snapshot.state)).toEqual(['QUEUED', 'RUNNING', 'COMPLETED'])
    expect(fetchJob).toHaveBeenCalledTimes(2)
  })

  it('需要核对调用结果时停止等待，不自动重新调用模型', async () => {
    const onUpdate = vi.fn()
    expect(await waitForJobCompletion(job('NEEDS_RECONCILIATION'), onUpdate)).toEqual(job('NEEDS_RECONCILIATION'))
    expect(fetchJob).not.toHaveBeenCalled()
  })

  it('进度读取只有限重试，明确返回错误，不重发创建任务', async () => {
    vi.useFakeTimers()
    const error = new ApiError(503, { code: 'TEMPORARY', message: '暂时无法读取进度' })
    vi.mocked(fetchJob).mockRejectedValue(error)
    const rejection = expect(waitForJobCompletion(job('QUEUED'), vi.fn())).rejects.toBe(error)
    await vi.runAllTimersAsync()
    await rejection
    expect(fetchJob).toHaveBeenCalledTimes(3)
  })
})
