import type { EstimateOut } from '../api/types'
import { CollapsibleBlock } from './CollapsibleBlock'

export function WindowPicker({ windows, selectedIds, onChange, disabled = false }: {
  windows: NonNullable<EstimateOut['windows']>
  selectedIds: string[]
  onChange: (ids: string[]) => void
  disabled?: boolean
}) {
  const selected = windows.filter((window) => selectedIds.includes(String(window.window_id)))
  const tokens = selected.reduce((total, window) => total + Number(window.estimated_tokens || 0), 0)
  return (
    <CollapsibleBlock title="窗口列表" summary={`共 ${windows.length} 个窗口；已选 ${selected.length} 个；失败 ${windows.filter(window => window.processing_status === 'failed').length} 个`}>
    <fieldset className="ndr-window-picker" data-testid="window-picker" disabled={disabled}>
      <legend>选择要处理的窗口（可多选）</legend>
      <p className="hint">这里只限制对白归属窗口；人物识别仍会读取本章全文。默认选择未完成窗口，已完成窗口无需重复处理；也可手动重做。</p>
      <div className="ndr-form-actions">
        <button type="button" onClick={() => onChange(windows.filter(window => window.processing_status !== 'completed').map(window => String(window.window_id)))}>只选未完成窗口</button>
        <button type="button" onClick={() => onChange(windows.filter(window => window.processing_status === 'failed').map(window => String(window.window_id)))}>只选失败窗口</button>
        <button type="button" onClick={() => onChange(windows.map((window) => String(window.window_id)))} data-testid="windows-select-all">全选窗口</button>
        <button type="button" onClick={() => onChange([])} data-testid="windows-clear">清空选择</button>
      </div>
      <p className="hint" data-testid="windows-selection-summary">
        已选 {selected.length}/{windows.length} 个窗口 · 约 {tokens.toLocaleString()} tokens（仅对白归属估算）
      </p>
      {windows.map((window) => {
        const id = String(window.window_id)
        return (
          <label key={id} className="ndr-window-option">
            <input type="checkbox" checked={selectedIds.includes(id)}
              onChange={(event) => onChange(event.target.checked ? [...selectedIds, id] : selectedIds.filter((item) => item !== id))}
              data-testid={`window-${id}`} />
            <span>
              窗口 {String(window.ordinal)} · {String(window.target_count)} 句对白 · 约 {Number(window.estimated_tokens).toLocaleString()} tokens
              <small className={window.processing_status === 'failed' ? 'status-error' : 'hint'}>
                {window.processing_status === 'completed' ? '已完成' : window.processing_status === 'failed' ? '失败' : '未完成'}
                {typeof window.processed_target_count === 'number' && ` · 已保存 ${window.processed_target_count}/${String(window.target_count)} 句`}
                {window.last_error ? ` · ${String(window.last_error)}` : ''}
              </small>
              <small>{String(window.preview || '（无文本预览）')}</small>
            </span>
          </label>
        )
      })}
    </fieldset>
    </CollapsibleBlock>
  )
}
