import { describe, expect, it } from 'vitest'

import { ApiError, readRetryDelay, shouldRetryReadRequest } from '../src/api/client'

describe('read query retry policy', () => {
  it('只重试网络错误、限流和服务端临时错误，且最多两次', () => {
    expect(shouldRetryReadRequest(0, new TypeError('network'))).toBe(true)
    expect(shouldRetryReadRequest(1, new ApiError(503, { code: 'TEMP', message: 'busy' }))).toBe(true)
    expect(shouldRetryReadRequest(0, new ApiError(429, { code: 'RATE', message: 'slow down' }))).toBe(true)
    expect(shouldRetryReadRequest(0, new ApiError(404, { code: 'NOT_FOUND', message: 'missing' }))).toBe(false)
    expect(shouldRetryReadRequest(2, new TypeError('network'))).toBe(false)
  })

  it('使用短退避且限制等待时间', () => {
    expect(readRetryDelay(0)).toBe(200)
    expect(readRetryDelay(1)).toBe(400)
    expect(readRetryDelay(10)).toBe(1_000)
  })
})
