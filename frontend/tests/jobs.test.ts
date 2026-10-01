import { describe, expect, it } from 'vitest'

import { budgetPayload, freshIdempotencyKey } from '../src/api/jobs'

describe('freshIdempotencyKey', () => {
  it('sends validation retries independently from unresolved quote rechecks', () => {
    expect(budgetPayload({ maxInputTokens: null, maxOutputTokens: null, maxRechecks: 2, maxFormatRetries: 4 }))
      .toEqual({ max_input_tokens: null, max_output_tokens: null, max_rechecks: 2, max_format_retries: 4 })
    expect(budgetPayload({ maxInputTokens: null, maxOutputTokens: null, maxRechecks: 0 }).max_format_retries).toBe(1)
  })
  it('creates a new bounded key for each intentional execution', () => {
    const payload = JSON.stringify({ chapterId: 'c1', profileId: 'p1' })
    const first = freshIdempotencyKey('roster:b1:c1', payload)
    const second = freshIdempotencyKey('roster:b1:c1', payload)

    expect(first).not.toBe(second)
    expect(first.startsWith('roster:b1:c1:')).toBe(true)
    expect(second.startsWith('roster:b1:c1:')).toBe(true)
    expect(first.length).toBeLessThanOrEqual(128)
    expect(second.length).toBeLessThanOrEqual(128)
  })

  it('keeps long scopes within the backend limit', () => {
    const key = freshIdempotencyKey(`roster:${'b'.repeat(100)}`, 'payload')
    expect(key.length).toBe(128)
  })
})
