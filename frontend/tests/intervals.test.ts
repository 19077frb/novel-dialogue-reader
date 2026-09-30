import { describe, expect, it } from 'vitest'
import { SpanIndex } from '../src/text/intervals'

describe('SpanIndex', () => {
  it('preserves linear-filter semantics and tie order for nested and empty spans', () => {
    let seed = 8765
    const random = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) % 110)
    const spans = Array.from({ length: 500 }, () => ({ start: random(), end: random() }))
    spans.push({ start: 0, end: 110 }, { start: 20, end: 40 }, { start: 20, end: 40 })
    const index = new SpanIndex(spans, row => row.start, row => row.end)
    for (let i = 0; i < 500; i++) {
      const start = random(), end = random()
      expect(index.overlapping(start, end)).toEqual(spans.filter(row => row.start < end && row.end > start))
    }
    expect(new SpanIndex([], () => 0, () => 0).overlapping(0, 1)).toEqual([])
  })
  it('reads endpoints only while building, not on every paragraph lookup', () => {
    let reads = 0
    const index = new SpanIndex(Array.from({ length: 10000 }, (_, i) => i),
      i => { reads++; return i * 2 }, i => { reads++; return i * 2 + 1 })
    for (let i = 0; i < 1000; i++) expect(index.overlapping(i * 2, i * 2 + 1)).toEqual([i])
    expect(reads).toBe(20000)
  })
})
