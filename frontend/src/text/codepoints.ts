/**
 * 码点 ↔ UTF-16 映射（DEVELOPMENT.md 4.1）。
 *
 * 后端所有坐标都是**码点**（canonical 全文按码点计数），而 JavaScript 字符串下标是
 * **UTF-16 单元**：`'😀'.length === 2`、`'𠮷'.length === 2`。直接 `text.slice(cp, cp)`
 * 会在含有 emoji / 扩展汉字时错位，所以这里统一提供换算与切片。
 */

/** 码点长度（`Array.from` 语义，但用迭代器避免额外数组）。 */
export function cpLength(text: string): number {
  let count = 0
  for (const _char of text) count += 1
  return count
}

/**
 * 把绝对码点位置换算成 `text` 里的 UTF-16 下标。
 * `baseCp` 是 `text` 起点对应的绝对码点；超出范围时夹到 `[0, text.length]`。
 */
export function utf16IndexForCp(text: string, baseCp: number, cp: number): number {
  const target = cp - baseCp
  if (target <= 0) return 0
  let count = 0
  let index = 0
  for (const char of text) {
    if (count === target) return index
    count += 1
    index += char.length
  }
  return text.length
}

/** 按码点切片：`fromCp`/`toCp` 是绝对码点，`baseCp` 是 `text` 起点的绝对码点。 */
export function sliceByCodepoints(
  text: string,
  baseCp: number,
  fromCp: number,
  toCp: number,
): string {
  const start = utf16IndexForCp(text, baseCp, fromCp)
  const end = utf16IndexForCp(text, baseCp, toCp)
  return text.slice(start, Math.max(start, end))
}