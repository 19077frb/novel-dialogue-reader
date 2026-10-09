import { describe, expect, it, vi } from 'vitest'

import { ApiError, apiRequest, readRetryDelay, shouldRetryReadRequest } from '../src/api/client'
import { fetchBooks } from '../src/api/books'

describe('read query retry policy', () => {
  it('保留顶层错误编号，内部错误展示编号且不改变其他业务错误文案', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'INTERNAL_ERROR', message: '服务器内部错误', details: {} }, request_id: 'request-123',
    }), { status: 500 }))
    try {
      const error = await apiRequest('/api/jobs', { method: 'POST', body: {} }).catch(value => value)
      expect(error).toBeInstanceOf(ApiError)
      if (!(error instanceof ApiError)) throw new Error('Expected ApiError')
      expect(error.requestId).toBe('request-123')
      expect(error.message).toBe('服务器内部错误（错误编号：request-123）')
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(new ApiError(409, { code: 'RESOURCE_CONFLICT', message: '窗口占用', request_id: 'id' }).message).toBe('窗口占用')
    } finally { fetchMock.mockRestore() }
  })
  it('书架分页保留默认地址并编码后续页游标', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify({ data: { items: [], next_cursor: null } }), { status: 200 },
    ))
    try {
      await fetchBooks()
      await fetchBooks(undefined, 'page 2/+')
      expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(['/api/books', '/api/books?cursor=page%202%2F%2B'])
    } finally { fetchMock.mockRestore() }
  })
  it('删除成功的 204 响应不尝试读取 JSON', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(null, { status: 204 }))
    try {
      await expect(apiRequest<void>('/api/books/b1', { method: 'DELETE' })).resolves.toBeUndefined()
    } finally {
      fetchMock.mockRestore()
    }
  })
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
