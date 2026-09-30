import { describe, expect, it } from 'vitest'
import { sentenceRanges } from '../src/text/sentences'

describe('sentence ranges', () => {
  it('preserves every character and attaches closing quotes to the preceding sentence', () => {
    const text = '「😀你好。第二句！？」\n尾句'
    const ranges = sentenceRanges(text, 10)
    expect(ranges.map(range => range.text)).toEqual(['「😀你好。', '第二句！？」\n', '尾句'])
    expect(ranges.map(range => range.text).join('')).toBe(text)
    expect(ranges[1].startCp).toBe(15)
    expect(ranges.at(-1)?.endCp).toBe(10 + Array.from(text).length)
  })
  it('handles empty, unpunctuated and line-separated paragraphs', () => {
    expect(sentenceRanges('')).toEqual([])
    expect(sentenceRanges('没有标点')[0].text).toBe('没有标点')
    expect(sentenceRanges('第一行\n第二行').map(item => item.text)).toEqual(['第一行\n', '第二行'])
  })
})
