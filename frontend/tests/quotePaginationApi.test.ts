import { describe, expect, it, vi } from 'vitest'

import { fetchQuotes } from '../src/api/books'

describe('候选引语分页请求', () => {
  it('保持默认请求兼容，并传递章节、编码游标和取消信号', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify({ data: { items: [], next_cursor: null } }), { status: 200 },
    ))
    try {
      const controller = new AbortController()
      await fetchQuotes('b1')
      await fetchQuotes('b1', { chapterId: 'c1', cursor: 'next /+', limit: 500 }, controller.signal)
      expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
        '/api/books/b1/quotes?limit=500',
        '/api/books/b1/quotes?chapter_id=c1&cursor=next+%2F%2B&limit=500',
      ])
      expect(fetchMock.mock.calls[1][1]?.signal).toBe(controller.signal)
    } finally { fetchMock.mockRestore() }
  })
})
