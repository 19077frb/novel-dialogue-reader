import { beforeEach, expect, it, vi } from 'vitest'
import { apiData } from '../src/api/client'
import { createJob, estimateRange } from '../src/api/jobs'
import type { DialogueStrategy } from '../src/api/jobs'
import { recheckQuote } from '../src/api/review'

vi.mock('../src/api/client', () => ({ apiData: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiData).mockResolvedValue({} as never) })

it.each<DialogueStrategy>(['legacy', 'complete', 'complete-review', 'complete-blocks', 'complete-blocks-review', 'complete-blocks-isolated', 'complete-blocks-isolated-review', 'complete-blocks-isolated-feedback-review'])('serializes %s consistently for estimate, actual job and bounded recheck', async strategy => {
  const input = { bookVersionId: 'v', range: { chapterId: 'c', startCp: 10, endCp: 90, dialogueStrategy: strategy },
    readingMode: 'initial' as const, budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 1 } }
  await estimateRange('b', input)
  await createJob({ ...input, bookId: 'b', mode: 'process', profileId: 'p', idempotencyKey: 'k' })
  const calls = vi.mocked(apiData).mock.calls
  const first = (calls[0][1] as { body: { range: Record<string, unknown> } }).body.range
  const second = (calls[1][1] as { body: { range: Record<string, unknown> } }).body.range
  expect(first).toEqual(second)
  expect(first).toMatchObject({ chapter_id: 'c', start_cp: 10, end_cp: 90 })
  if (strategy === 'legacy') expect(first).not.toHaveProperty('output_protocol')
  else expect(first).toMatchObject({ context_policy: strategy.startsWith('complete-blocks') ? 'context-chapter-2' : 'context-chapter-1', output_protocol: 'expression-production-1' })
  expect(first.review_protocol === 'expression-evidence-review-1').toBe(strategy.endsWith('-review'))
  expect(first.auxiliary_protocol).toBe(strategy.includes('-isolated') ? 'expression-auxiliary-isolation-1' : undefined)
  expect(first.identity_feedback_protocol).toBe(strategy === 'complete-blocks-isolated-feedback-review' ? 'identity-feedback-1' : undefined)
  await recheckQuote('q', { profileId: 'p', idempotencyKey: 'r', dialogueStrategy: strategy, maxRecheckRounds: 1 })
  const local = (calls[2][1] as { body: Record<string, unknown> }).body
  expect(local.dialogue_strategy).toBe(strategy === 'legacy' ? undefined : strategy)
  expect(local).not.toHaveProperty('range')
})

it('rejects zero-round feedback configuration before estimate, task or local paid request', async () => {
  const input = { range: { chapterId: 'c', startCp: 0, endCp: 10, dialogueStrategy: 'complete-blocks-isolated-feedback-review' as const },
    readingMode: 'reread' as const, budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 0 } }
  await expect(estimateRange('b', input)).rejects.toThrow('至少 1')
  await expect(createJob({ ...input, bookId: 'b', mode: 'process', idempotencyKey: 'k' })).rejects.toThrow('至少 1')
  await expect(recheckQuote('q', { profileId: 'p', idempotencyKey: 'r', dialogueStrategy: input.range.dialogueStrategy })).rejects.toThrow('至少 1')
  expect(apiData).not.toHaveBeenCalled()
})
