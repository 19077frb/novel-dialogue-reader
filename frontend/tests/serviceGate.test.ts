import { beforeEach, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { ApiError } from '../src/api/client'
import type { JobDetailOut } from '../src/api/types'
import { assertServiceAvailable, clearServiceBlock, getServiceBlock, getServiceBlocks,
  jobServiceErrorCode, observeServiceError, observeServiceJob, submitServiceJob, useServiceBlocks, getServiceGateStatus } from '../src/processing/serviceGate'
import { writeJournal } from '../src/processing/journal'

beforeEach(() => {
  localStorage.clear()
  const locks = new Map<string, Promise<unknown>>()
  Object.defineProperty(navigator, 'locks', { configurable: true, value: {
    request: (key: string, action: () => Promise<unknown>) => {
      const current = (locks.get(key) ?? Promise.resolve()).then(action)
      locks.set(key, current.catch(() => undefined))
      return current
    },
  } })
})
const failure = (runId: string, code = 'RATE_LIMITED') => ({ id: 'job', updated_at: '2026-01-01',
  state: 'FAILED', service_error_code: code, service_error_run_id: runId } as JobDetailOut)

it('classifies structured codes and HTTP statuses without guessing translated text', async () => {
  await observeServiceError('normal', new Error('提供方返回 429，额度不足'))
  expect(getServiceBlock('normal')).toBeNull()
  await observeServiceError('limited', new ApiError(429, { code: 'HTTP_429', message: '限流' }))
  await observeServiceError('quota', new ApiError(402, { code: 'HTTP_402', message: '额度不足' }))
  expect(getServiceBlock('limited')?.code).toBe('RATE_LIMITED')
  expect(getServiceBlock('quota')?.code).toBe('PROVIDER_QUOTA_EXHAUSTED')
  expect(jobServiceErrorCode({ last_error: '人物分析失败：提供方返回 429' } as JobDetailOut)).toBeNull()
  expect(jobServiceErrorCode({ last_error: 'RATE_LIMITED: old', service_error_code: null } as JobDetailOut)).toBeNull()
  expect(jobServiceErrorCode({ last_error: 'RATE_LIMITED: old' } as JobDetailOut)).toBe('RATE_LIMITED')
})

it('persists independent profile blocks and serializes failure publication with stale clears', async () => {
  await Promise.all([observeServiceJob('p1', failure('first')), observeServiceJob('p2', failure('other'))])
  const first = getServiceBlock('p1')!
  await Promise.all([observeServiceJob('p1', failure('second')),
    expect(clearServiceBlock('p1', first.id)).rejects.toThrow('新的问题')])
  expect(getServiceBlocks()).toHaveLength(2)
  expect(getServiceBlock('p1')?.id).not.toBe(first.id)
  expect(() => assertServiceAvailable('p2')).toThrow('模型服务限流')
  expect(() => assertServiceAvailable('p3')).not.toThrow()
})

it('keeps acknowledged failures across reload while new runs close the gate again', async () => {
  await observeServiceJob('p1', { ...failure('review-failed'), state: 'COMPLETED' })
  const block = getServiceBlock('p1')!
  await clearServiceBlock('p1', block.id)
  await observeServiceJob('p1', failure('review-failed'))
  expect(getServiceBlock('p1')).toBeNull()
  await observeServiceJob('p1', failure('new-review-failed'))
  expect(getServiceBlock('p1')?.id).not.toBe(block.id)
})

it('waits for job completion before blocking a transient failure that its active retry can resolve', async () => {
  await observeServiceJob('p1', { ...failure('transient-run'), state: 'RUNNING' })
  expect(getServiceBlock('p1')).toBeNull()
  await observeServiceJob('p1', { ...failure('success-run'), state: 'COMPLETED', service_error_code: null })
  expect(getServiceBlock('p1')).toBeNull()
})

it('checks shared storage immediately before POST and lets an acknowledged in-flight call finish', async () => {
  let finish!: (job: JobDetailOut) => void
  const post = vi.fn(() => new Promise<JobDetailOut>(resolve => { finish = resolve }))
  const inFlight = submitServiceJob('p1', post)
  await observeServiceJob('p1', failure('other-book'))
  finish({ id: 'accepted', state: 'COMPLETED' } as JobDetailOut)
  await expect(inFlight).resolves.toMatchObject({ id: 'accepted' })
  await expect(submitServiceJob('p1', post)).rejects.toThrow('模型服务限流')
  expect(post).toHaveBeenCalledTimes(1)
})

it('records a rejected POST before another queued request can dispatch', async () => {
  const post = vi.fn().mockRejectedValue(new ApiError(402, { code: 'PROVIDER_QUOTA_EXHAUSTED', message: 'quota' }))
  await expect(submitServiceJob('p1', post)).rejects.toThrow('模型服务额度不足')
  await expect(submitServiceJob('p1', post)).rejects.toThrow('模型服务额度不足')
  expect(post).toHaveBeenCalledTimes(1)
})

it('updates a spectator from another tab storage change and refuses POST with that persisted block', () => {
  const view = renderHook(() => useServiceBlocks())
  const block = { id: 'other-tab-block', profileId: 'p1', code: 'RATE_LIMITED', createdAt: 1 }
  act(() => {
    writeJournal('service-gate:p1', { block, observedFailures: ['other-tab-run'] })
    window.dispatchEvent(new StorageEvent('storage', { key: 'ndr:tasks:v1:service-gate:p1' }))
  })
  expect(view.result.current).toEqual([block])
  expect(() => assertServiceAvailable('p1')).toThrow('模型服务限流')
  act(() => {
    writeJournal('service-gate:p1', { block: null, observedFailures: ['other-tab-run'] })
    window.dispatchEvent(new StorageEvent('storage', { key: 'ndr:tasks:v1:service-gate:p1' }))
  })
  expect(view.result.current).toEqual([])
  view.unmount()
})

it('keeps corrupt records visible as errors while submission remains closed for the damaged profile', async () => {
  localStorage.setItem('ndr:tasks:v1:service-gate:p1', '{broken')
  localStorage.setItem('ndr:tasks:v1:service-gate:%', '{}')
  await observeServiceJob('p2', failure('valid-run'))
  const status = getServiceGateStatus()
  expect(status.errors).toHaveLength(2)
  expect(status.blocks.map(block => block.profileId)).toEqual(['p2'])
  const view = renderHook(() => useServiceBlocks())
  expect(view.result.current.map(block => block.profileId)).toEqual(['p2'])
  await expect(submitServiceJob('p1', vi.fn())).rejects.toThrow('保存的任务记录无法读取')
  expect(() => assertServiceAvailable('p3')).not.toThrow()
  view.unmount()
})
