import type { ProcessingPreferences } from '../processing/preferences'
import { DisabledHint, disabledHint } from './DisabledHint'
import { useGeneralSettings } from '../settings/preferences'

export function RosterRepairSettings({ preferences, onChange, disabled = false, experimentalEnabled, compact = false }: {
  preferences: ProcessingPreferences
  onChange: (patch: Partial<ProcessingPreferences>) => void
  disabled?: boolean
  experimentalEnabled?: boolean
  compact?: boolean
}) {
  const [settings] = useGeneralSettings()
  if (!(experimentalEnabled ?? settings.enableExperimentalFeatures)) return null
  const reason = '当前处理任务尚未结束，请等待完成或先停止任务再修改人物分析配置。'
  const settingHint = (description: string) => disabledHint([description, disabled && reason].filter(Boolean).join(' '))
  return <section className={compact ? 'ndr-experiment-details' : 'card'} aria-label="人物证据修复">
    <label><input type="checkbox" data-testid="roster-repair-enabled" checked={preferences.rosterRepairEnabled}
      disabled={disabled} {...settingHint('保留已通过校验的结果。单章、批量与自动处理共用，新建人物任务使用此设置。')}
      onChange={event => onChange({ rosterRepairEnabled: event.target.checked })} />{compact ? '人物证据定向修复' : '启用人物证据定向修复（试验）'}</label>
    <p className="hint">修复证据校验失败的人物组，模型判断仍需核对。</p>
    {preferences.rosterRepairEnabled && <div className="ndr-range-grid">
      <label className="ndr-field">人物证据最多修复次数（0 关闭修复）
        <input type="number" min={0} max={5} value={preferences.maxRosterRepairs} disabled={disabled}
          {...settingHint('证据修复最多 5 次，与首次分析的格式重试分别计数；修复返回格式错误也占用修复次数。输出上限和思考模式沿用当前配置。')}
          onChange={event => onChange({ maxRosterRepairs: Number(event.target.value) })} />
      </label>
      {!compact && <label className="ndr-field">校验失败后的重试次数（与对白共用）
        <input type="number" min={0} max={5} value={preferences.maxFormatRetries} disabled={disabled}
          {...settingHint('首次分析的格式重试最多 5 次，与证据修复分别计数；此设置也用于对白。批量估算包含这些额外阶段。')}
          onChange={event => onChange({ maxFormatRetries: Number(event.target.value) })} />
      </label>}
    </div>}
    {preferences.rosterRepairEnabled && <p className="hint">额外调用会增加用量与等待；用量未知时暂停核对，实际用量可能超出估算。</p>}
    <DisabledHint reason={disabled && reason} />
  </section>
}
