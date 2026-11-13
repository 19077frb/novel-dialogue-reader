import { beforeEach, expect, it, vi } from 'vitest'
import { apiData } from '../src/api/client'
import { preflightDialogue } from '../src/api/jobs'
import type { DialogueStrategy } from '../src/api/jobs'

vi.mock('../src/api/client', () => ({ apiData: vi.fn() }))

beforeEach(() => { vi.resetAllMocks() })

it.each<[DialogueStrategy | undefined, string]>([
  [undefined, 'context-1'], ['legacy', 'context-1'],
  ['complete-review', 'context-chapter-1'],
  ['complete-blocks-isolated-feedback-review', 'context-chapter-3'],
])('uses the execution strategy and exact selection in the read-only request (%s)', async (strategy, policy) => {
  const signal = new AbortController().signal
  await preflightDialogue({ bookId: 'book', bookVersionId: 'version',
    range: { chapterId: 'chapter', startCp: 15, endCp: 90, dialogueStrategy: strategy },
    selectedWindowIds: ['selected-window'], readingMode: 'reread', visibleHorizonCp: null }, signal)
  expect(apiData).toHaveBeenCalledWith('/api/jobs/preflight', {
    method: 'POST', signal, body: {
      book_id: 'book', book_version_id: 'version',
      range: { chapter_id: 'chapter', start_cp: 15, end_cp: 90, context_policy: policy },
      selected_window_ids: ['selected-window'], reading_mode: 'reread', visible_horizon_cp: null,
    },
  })
})
