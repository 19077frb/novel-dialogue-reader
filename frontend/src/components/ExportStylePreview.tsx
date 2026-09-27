/** 导出样式选择与样张说明（T15B：颜色/编号/两者）。 */
import type { ExportStylePreset } from '../api/types'
import { SPEAKER_COLORS } from '../styles/palette'

const PRESETS: { value: ExportStylePreset; label: string; hint: string }[] = [
  {
    value: 'color_and_label',
    label: '颜色 + 编号',
    hint: '默认：颜色用于区分说话人，编号在灰度或颜色被覆盖时仍然可读。',
  },
  { value: 'color_only', label: '仅颜色', hint: '适合彩色阅读器；灰度打印时可能难以区分。' },
  { value: 'label_only', label: '仅编号', hint: '黑白打印或色弱场景更稳。' },
]

export interface ExportStylePreviewProps {
  value: ExportStylePreset
  onChange: (value: ExportStylePreset) => void
}

export function ExportStylePreview({ value, onChange }: ExportStylePreviewProps) {
  const color = SPEAKER_COLORS[0]
  const withColor = value !== 'label_only'
  const withLabel = value !== 'color_only'
  return (
    <fieldset className="ndr-export-style" data-testid="export-style">
      <legend>标注样式</legend>
      {PRESETS.map((preset) => (
        <label key={preset.value}>
          <input
            type="radio"
            name="export-style"
            checked={value === preset.value}
            onChange={() => onChange(preset.value)}
            data-testid={`export-style-${preset.value}`}
          />
          {preset.label}
          <span className="hint">{preset.hint}</span>
        </label>
      ))}
      <p className="ndr-export-style-sample" data-testid="export-style-sample">
        <span
          style={withColor ? { color } : undefined}
          data-testid="style-sample-quote"
          data-style={value}
        >
          {withLabel ? <span className="label">〔S1〕</span> : null}
          「雨停了。」
        </span>
      </p>
    </fieldset>
  )
}