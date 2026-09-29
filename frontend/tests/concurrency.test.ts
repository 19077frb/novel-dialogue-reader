import { describe, expect, it } from 'vitest'

import { createTaskLimiter, mapWithConcurrency } from '../src/processing/concurrency'

describe('processing concurrency', () => {
  it('limits parallel workers and preserves result order', async () => {
    let active = 0
    let peak = 0
    const result = await mapWithConcurrency([3, 1, 2, 4], 2, async (value) => {
      active += 1
      peak = Math.max(peak, active)
      await new Promise((resolve) => setTimeout(resolve, value))
      active -= 1
      return value * 2
    })
    expect(peak).toBe(2)
    expect(result).toEqual([6, 2, 4, 8])
  })

  it('shares one limit across differently scheduled task types', async () => {
    const limiter = createTaskLimiter(2)
    let active = 0
    let peak = 0
    const task = (name: string) =>
      limiter.run(async () => {
        active += 1
        peak = Math.max(peak, active)
        await new Promise((resolve) => setTimeout(resolve, 2))
        active -= 1
        return name
      })
    expect(await Promise.all([task('人物'), task('窗口一'), task('窗口二')])).toEqual([
      '人物',
      '窗口一',
      '窗口二',
    ])
    expect(peak).toBe(2)
  })
})
