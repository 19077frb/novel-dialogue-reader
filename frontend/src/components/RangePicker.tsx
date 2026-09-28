import type { ChapterOut } from '../api/types'

/**
 * 范围选择：按章选择或手填码点范围。
 * 纯前端状态：改范围只影响估算/预览请求，不会自动触发任何模型调用。
 */
export interface RangeValue {
  chapterId: string | null
  startCp: number
  endCp: number | null
}

export interface RangePickerProps {
  chapters: ChapterOut[]
  value: RangeValue
  canonicalLengthCp: number
  onChange: (value: RangeValue) => void
}

export function RangePicker({ chapters, value, canonicalLengthCp, onChange }: RangePickerProps) {
  const selectChapter = (chapterId: string) => {
    if (chapterId === '') {
      onChange({ chapterId: null, startCp: 0, endCp: null })
      return
    }
    const chapter = chapters.find((item) => item.id === chapterId)
    if (!chapter) return
    onChange({ chapterId, startCp: chapter.start_cp, endCp: chapter.end_cp })
  }

  return (
    <fieldset className="ndr-range-picker" data-testid="range-picker">
      <legend>处理范围</legend>
      <label>
        章节
        <select
          value={value.chapterId ?? ''}
          onChange={(event) => selectChapter(event.target.value)}
          data-testid="range-chapter"
        >
          <option value="">整本（0 – {canonicalLengthCp}）</option>
          {chapters.map((chapter) => (
            <option key={chapter.id} value={chapter.id}>
              {chapter.ordinal + 1}. {chapter.title}（{chapter.start_cp} – {chapter.end_cp}）
            </option>
          ))}
        </select>
      </label>
      <div className="ndr-range-cp">
        <label>
          起始码点
          <input
            type="number"
            min={0}
            max={canonicalLengthCp}
            value={value.startCp}
            onChange={(event) =>
              onChange({ ...value, chapterId: null, startCp: Number(event.target.value) || 0 })
            }
            data-testid="range-start-cp"
          />
        </label>
        <label>
          结束码点（留空=到书末）
          <input
            type="number"
            min={1}
            max={canonicalLengthCp}
            value={value.endCp ?? ''}
            placeholder={String(canonicalLengthCp)}
            onChange={(event) => {
              const raw = event.target.value
              onChange({
                ...value,
                chapterId: null,
                endCp: raw === '' ? null : Number(raw),
              })
            }}
            data-testid="range-end-cp"
          />
        </label>
      </div>
      <p className="hint" data-testid="range-summary">
        本次范围：{value.startCp} – {value.endCp ?? canonicalLengthCp}（共{' '}
        {Math.max(0, (value.endCp ?? canonicalLengthCp) - value.startCp)} 码点）
      </p>
    </fieldset>
  )
}