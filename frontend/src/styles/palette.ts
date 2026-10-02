/** 说话人配色：已确认的同一人物跨场景共享色号，匿名分组保持隔离。 */

export const SPEAKER_COLORS = [
  ...Array.from({ length: 16 }, (_, index) => `var(--ndr-speaker-${index})`),
] as const

export const UNKNOWN_COLOR = 'var(--ndr-muted)'

/** 取色号对应颜色；没有色号（未知/不可见）时返回 undefined，表示不着色。 */
export function colorForIndex(index: number | null | undefined): string | undefined {
  if (index === null || index === undefined) return undefined
  if (!Number.isSafeInteger(index) || index < 0) return undefined
  if (index < SPEAKER_COLORS.length) return SPEAKER_COLORS[index]
  const hue = Math.floor((((index - SPEAKER_COLORS.length + 1) * 137.508) % 360) * 1000) / 1000
  return `hsl(${hue} var(--ndr-speaker-saturation) var(--ndr-speaker-lightness))`
}

export function labelText(label: string | null | undefined): string {
  return label ? `〔${label}〕` : ''
}
