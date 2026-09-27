/** 说话人配色（T11）：场景内按分组稳定分配，超出色板时依赖编号辨认。 */

export const SPEAKER_COLORS = [
  '#2f6feb',
  '#d97706',
  '#16a34a',
  '#dc2626',
  '#7c3aed',
  '#0891b2',
  '#ca8a04',
  '#db2777',
] as const

export const UNKNOWN_COLOR = '#6b7280'

/** 取色号对应颜色；没有色号（未知/不可见）时返回 undefined，表示不着色。 */
export function colorForIndex(index: number | null | undefined): string | undefined {
  if (index === null || index === undefined) return undefined
  return SPEAKER_COLORS[index % SPEAKER_COLORS.length]
}

export function labelText(label: string | null | undefined): string {
  return label ? `〔${label}〕` : ''
}