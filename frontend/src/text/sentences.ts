/** Display-only sentence boundaries in Unicode codepoints; canonical text stays unchanged. */
export function sentenceRanges(text: string, baseCp = 0) {
  const chars = Array.from(text)
  const ranges: { startCp: number; endCp: number; text: string }[] = []
  let start = 0
  for (let index = 0; index < chars.length; index++) {
    if (!/[。！？!?\n]/.test(chars[index])) continue
    while (index + 1 < chars.length && /[。！？!?」』”’）)\n]/.test(chars[index + 1])) index++
    ranges.push({ startCp: baseCp + start, endCp: baseCp + index + 1, text: chars.slice(start, index + 1).join('') })
    start = index + 1
  }
  if (start < chars.length) ranges.push({ startCp: baseCp + start, endCp: baseCp + chars.length, text: chars.slice(start).join('') })
  return ranges
}
