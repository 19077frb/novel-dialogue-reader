import { describe, expect, it } from 'vitest'

import { budgetPayload, dialogueStrategyPayload, freshIdempotencyKey } from '../src/api/jobs'

describe('freshIdempotencyKey', () => {
  it('maps the same explicit strategy for estimate and execution without upgrading restored inputs', () => {
    expect(dialogueStrategyPayload()).toEqual({})
    expect(dialogueStrategyPayload('legacy')).toEqual({})
    expect(dialogueStrategyPayload('future' as Parameters<typeof dialogueStrategyPayload>[0])).toEqual({})
    expect(dialogueStrategyPayload('complete')).toEqual({ context_policy: 'context-chapter-1', output_protocol: 'expression-production-1' })
    expect(dialogueStrategyPayload('complete-review')).toEqual({ context_policy: 'context-chapter-1', output_protocol: 'expression-production-1', review_protocol: 'expression-evidence-review-1' })
    expect(dialogueStrategyPayload('complete-blocks')).toEqual({ context_policy: 'context-chapter-2', output_protocol: 'expression-production-1' })
  })
  it('sends validation retries independently from unresolved quote rechecks', () => {
    expect(budgetPayload({ maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 2, maxFormatRetries: 4 }))
      .toEqual({ max_input_tokens: null, max_output_tokens: null, max_recheck_rounds: 2, max_format_retries: 4 })
    expect(budgetPayload({ maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: 0 }).max_format_retries).toBe(1)
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

  it('preserves old restored budgets but gives explicit rounds precedence', () => {
    const legacy = { maxInputTokens: null, maxOutputTokens: null, maxRechecks: 2 } as Parameters<typeof budgetPayload>[0]
    expect(budgetPayload(legacy)).toMatchObject({ max_rechecks: 2 })
    expect(budgetPayload({ ...legacy, maxRecheckRounds: 0 })).toMatchObject({ max_recheck_rounds: 0 })
    expect(budgetPayload({ ...legacy, maxRecheckRounds: 0 })).not.toHaveProperty('max_rechecks')
  })

  it('keeps long scopes within the backend limit', () => {
    const key = freshIdempotencyKey(`roster:${'b'.repeat(100)}`, 'payload')
    expect(key.length).toBe(128)
  })
})
