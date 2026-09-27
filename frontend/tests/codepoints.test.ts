import { describe, expect, it } from 'vitest'

import { cpLength, sliceByCodepoints, utf16IndexForCp } from '../src/text/codepoints'

/** F11：emoji 与扩展汉字在 UTF-16 里占 2 个单元、1 个码点。 */
describe('码点与 UTF-16 映射', () => {
  it('cpLength 按码点数而不是 UTF-16 单元数', () => {
    expect(cpLength('abc')).toBe(3)
    expect(cpLength('😀𠮷')).toBe(2)
    expect('😀𠮷'.length).toBe(4)
    expect(cpLength('𠮷野家的猫🐈跳上窗台。')).toBe(11)
    expect('𠮷野家的猫🐈跳上窗台。'.length).toBe(13)
  })

  it('utf16IndexForCp 把码点位置换算成字符串下标', () => {
    const text = '😀ab'
    expect(utf16IndexForCp(text, 10, 10)).toBe(0) // 起点
    expect(utf16IndexForCp(text, 10, 11)).toBe(2) // 跳过 emoji
    expect(utf16IndexForCp(text, 10, 12)).toBe(3) // 跳过 emoji + a
    expect(utf16IndexForCp(text, 10, 99)).toBe(text.length) // 超出右端
    expect(utf16IndexForCp(text, 10, 5)).toBe(0) // 超出左端
  })

  it('sliceByCodepoints 在 astral 字符前后都切得准', () => {
    const text = '𠮷野家的猫🐈跳上窗台。'
    expect(sliceByCodepoints(text, 0, 0, 1)).toBe('𠮷')
    expect(sliceByCodepoints(text, 0, 1, 4)).toBe('野家的')
    expect(sliceByCodepoints(text, 0, 5, 6)).toBe('🐈')
    expect(sliceByCodepoints(text, 0, 0, 11)).toBe(text)
    // 与 Python 的码点切片一致：后端给的 [start_cp, end_cp) 直接可用
    expect(sliceByCodepoints(text, 100, 105, 106)).toBe('🐈')
  })
})