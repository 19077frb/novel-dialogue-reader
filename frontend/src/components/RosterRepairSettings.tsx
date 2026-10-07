import type { ProcessingPreferences } from '../processing/preferences'
import { DisabledHint, disabledHint } from './DisabledHint'

export function RosterRepairSettings({ preferences, onChange, disabled = false }: {
  preferences: ProcessingPreferences
  onChange: (patch: Partial<ProcessingPreferences>) => void
  disabled?: boolean
}) {
  const reason = '当前处理任务尚未结束，请等待完成或先停止任务再修改人物分析配置。'
  return <section className="card" aria-label="人物证据修复">
    <label><input type="checkbox" data-testid="roster-repair-enabled" checked={preferences.rosterRepairEnabled}
      disabled={disabled} {...disabledHint(disabled && reason)}
      onChange={event => onChange({ rosterRepairEnabled: event.target.checked })} />启用人物证据定向修复（试验）</label>
    <p className="hint">默认关闭。开启后保留校验有效的人物，仅修复证据不合法的相关人物组；不代表模型判断一定正确。单章、批量与自动处理共用，只影响之后创建的人物任务，不用于人物合并或局部对白复核。</p>
    {preferences.rosterRepairEnabled && <div className="ndr-range-grid">
      <label className="ndr-field">人物证据最多修复次数（0 关闭修复）
        <input type="number" min={0} max={5} value={preferences.maxRosterRepairs} disabled={disabled}
          {...disabledHint(disabled && reason)} onChange={event => onChange({ maxRosterRepairs: Number(event.target.value) })} />
      </label>
      <label className="ndr-field">校验失败后的重试次数（与对白共用）
        <input type="number" min={0} max={5} value={preferences.maxFormatRetries} disabled={disabled}
          {...disabledHint(disabled && reason)} onChange={event => onChange({ maxFormatRetries: Number(event.target.value) })} />
      </label>
    </div>}
    {preferences.rosterRepairEnabled && <p className="hint">证据修复与首次分析的格式重试分别计数，最多各 5 次；修复返回格式错误也占用修复次数，不另追加重试。会增加模型调用、Token 消耗和等待；用量未知时暂停核对。不会自动增加模型输出上限或开启思考。批量估算包含这些额外阶段，但不是实际消耗的上限。</p>}
    <DisabledHint reason={disabled && reason} />
  </section>
}
