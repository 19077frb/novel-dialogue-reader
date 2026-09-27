import type { SpeakerLegendItemOut } from '../api/types'
import { colorForIndex, labelText } from '../styles/palette'

/**
 * 说话人图例（T11）：完全由后端 `legend` 驱动。
 *
 * - 每个分组给一个场景内稳定的色号；颜色和编号只来自真实投影，不在这里凭空生成。
 * - 点击某一项是纯前端定位（滚动到该分组首次发言处），不会发起任何模型调用。
 */
export interface SpeakerLegendProps {
  legend: SpeakerLegendItemOut[]
  onFocus?: (item: SpeakerLegendItemOut) => void
}

export function SpeakerLegend({ legend, onFocus }: SpeakerLegendProps) {
  if (legend.length === 0) {
    return (
      <p className="hint" data-testid="speaker-legend-empty">
        当前范围内还没有可显示的说话人分组。
      </p>
    )
  }
  return (
    <ul className="ndr-speaker-legend" data-testid="speaker-legend">
      {legend.map((item) => {
        const color = colorForIndex(item.color_index)
        return (
          <li key={item.group_id} data-testid="speaker-legend-item" data-group-id={item.group_id}>
            <button
              type="button"
              className="ndr-legend-button"
              onClick={onFocus ? () => onFocus(item) : undefined}
              disabled={!onFocus}
              title={item.description || `首次发言 ${item.first_quote_id ?? ''}`}
            >
              <span
                className="ndr-legend-swatch"
                data-testid="speaker-swatch"
                style={color ? { background: color } : undefined}
                aria-hidden="true"
              />
              <span className="ndr-legend-label">{labelText(item.label)}</span>
              <span className="hint">{item.label}</span>
              <span className="hint">{item.quote_count} 句</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}