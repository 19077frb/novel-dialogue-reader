import { useProcessingPreferences } from '../processing/preferences'
import type { ProcessingPreferences } from '../processing/preferences'
import type { ModelProfileOut } from '../api/types'

export function ThinkingSettings({ disabled, profiles, profileId, onProfileChange, profileTestId }: {
  disabled: boolean
  profiles: ModelProfileOut[]
  profileId: string
  onProfileChange: (id: string) => void
  profileTestId: string
}) {
  const profile = profiles.find(item => item.id === profileId)
  const [preferences, update] = useProcessingPreferences()
  const params = profile?.params ?? {}
  const thinking = params.thinking as { type?: string } | undefined
  const labels: Record<string, string> = { disabled: '关闭', enabled: '开启', adaptive: '自适应', low: '低', medium: '中', high: '高' }
  const effectivelyDisabled = preferences.thinkingMode === 'disabled'
    || (preferences.thinkingMode === 'default' && thinking?.type === 'disabled')
  return <section className="card" data-testid="model-thinking-settings">
    <h3>模型配置与本次思考设置</h3>
    <label className="ndr-field">模型配置
      <select value={profileId} disabled={disabled} onChange={event => onProfileChange(event.target.value)} data-testid={profileTestId}>
        <option value="">（请选择模型配置）</option>
        {profiles.map(item => <option key={item.id} value={item.id}>{item.name} · {item.protocol} · {item.model}</option>)}
      </select>
    </label>
    {profiles.length === 0 && <p className="hint" data-testid="preview-no-profile">还没有模型配置：请先到“模型配置”添加提供方（真实提供方需要密钥）。</p>}
    <div className="ndr-form-actions">
      <label className="ndr-field">思考模式
        <select data-testid="processing-thinking-mode" disabled={disabled} value={preferences.thinkingMode}
          onChange={event => update({ thinkingMode: event.target.value as ProcessingPreferences['thinkingMode'] })}>
          <option value="default">沿用模型配置</option><option value="disabled">关闭</option>
          <option value="enabled">开启</option><option value="adaptive">自适应</option>
        </select>
      </label>
      <label className="ndr-field">思考强度
        <select data-testid="processing-thinking-effort" disabled={disabled || effectivelyDisabled} value={preferences.thinkingEffort}
          onChange={event => update({ thinkingEffort: event.target.value as ProcessingPreferences['thinkingEffort'] })}>
          <option value="default">沿用模型配置</option><option value="low">低</option>
          <option value="medium">中</option><option value="high">高</option>
        </select>
      </label>
    </div>
    {profile && <p className="hint" data-testid="profile-thinking-defaults">当前配置「{profile.name}」默认：模式 {labels[thinking?.type ?? ''] ?? '模型服务默认'}；强度 {labels[String(params.reasoning_effort ?? '')] ?? '模型服务默认'}。</p>}
    <p className="hint">单章、人物识别、试运行、批量与局部复核共用并记忆此设置；不修改已保存的模型配置或运行中任务。
      思考功能需要模型服务支持，不确定时请选择沿用模型配置。
      沿用模型配置时使用模型配置页保存的默认值；未指定则由模型服务决定。关闭思考时不发送强度。开启思考可能增加用量与等待时间，不会自动提高输出上限。</p>
  </section>
}
