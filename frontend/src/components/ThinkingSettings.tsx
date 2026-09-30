import { useProcessingPreferences } from '../processing/preferences'
import type { ProcessingPreferences } from '../processing/preferences'

export function ThinkingSettings({ disabled }: { disabled: boolean }) {
  const [preferences, update] = useProcessingPreferences()
  return <section className="card">
    <h3>本次模型思考设置</h3>
    <div className="ndr-form-actions">
      <label className="ndr-field">思考模式
        <select data-testid="processing-thinking-mode" disabled={disabled} value={preferences.thinkingMode}
          onChange={event => update({ thinkingMode: event.target.value as ProcessingPreferences['thinkingMode'] })}>
          <option value="default">沿用模型配置</option><option value="disabled">关闭</option>
          <option value="enabled">开启</option><option value="adaptive">自适应</option>
        </select>
      </label>
      <label className="ndr-field">思考强度
        <select data-testid="processing-thinking-effort" disabled={disabled || preferences.thinkingMode === 'disabled'} value={preferences.thinkingEffort}
          onChange={event => update({ thinkingEffort: event.target.value as ProcessingPreferences['thinkingEffort'] })}>
          <option value="default">沿用模型配置</option><option value="low">低</option>
          <option value="medium">中</option><option value="high">高</option>
        </select>
      </label>
    </div>
    <p className="hint">单章、人物识别、试运行与批量共用并记忆此设置；不修改已保存的模型配置或运行中任务。
      模式发送 thinking.type，强度发送 reasoning_effort；需提供方支持，不支持时请选择沿用配置。
      关闭思考时不发送强度。开启思考可能增加用量与等待时间，不会自动提高输出上限。</p>
  </section>
}
