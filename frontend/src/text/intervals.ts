/** Indexed half-open interval lookup, preserving input order for nested-span ties. */
export class SpanIndex<T> {
  private rows: { start: number; end: number; ordinal: number; item: T }[]
  private maxEnds: number[]

  constructor(items: T[], start: (item: T) => number, end: (item: T) => number) {
    this.rows = items.map((item, ordinal) => ({ start: start(item), end: end(item), ordinal, item }))
      .sort((a, b) => a.start - b.start || a.ordinal - b.ordinal)
    let maximum = -Infinity
    this.maxEnds = this.rows.map((row) => (maximum = Math.max(maximum, row.end)))
  }

  overlapping(start: number, end: number): T[] {
    let low = 0
    let high = this.rows.length
    while (low < high) {
      const middle = (low + high) >>> 1
      if (this.maxEnds[middle] <= start) low = middle + 1
      else high = middle
    }
    const left = low
    high = this.rows.length
    while (low < high) {
      const middle = (low + high) >>> 1
      if (this.rows[middle].start < end) low = middle + 1
      else high = middle
    }
    return this.rows.slice(left, low).filter((row) => row.start < end && row.end > start)
      .sort((a, b) => a.ordinal - b.ordinal).map((row) => row.item)
  }
}
