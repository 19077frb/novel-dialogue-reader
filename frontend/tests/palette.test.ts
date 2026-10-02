import { expect, it } from 'vitest'
import { colorForIndex } from '../src/styles/palette'

it('颜色由程序确定，不再每八人重复，基础色使用明暗设计令牌', () => {
  const colors = Array.from({ length: 1000 }, (_, index) => colorForIndex(index))
  expect(new Set(colors).size).toBe(1000)
  expect(colors[0]).toBe('var(--ndr-speaker-0)')
  expect(colors[8]).toBe('var(--ndr-speaker-8)')
  expect(colors[16]).toBe('hsl(137.508 var(--ndr-speaker-saturation) var(--ndr-speaker-lightness))')
  expect(colorForIndex(null)).toBeUndefined()
  expect(colorForIndex(-1)).toBeUndefined()
})
