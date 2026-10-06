import type { DialogueStrategy } from '../api/jobs'
import { dialogueStrategyDisabledReason } from '../api/jobs'
import { DisabledHint, disabledHint } from './DisabledHint'

export function DialogueStrategySettings({ value, onChange, disabled = false, disabledReason, rounds }: {
  value: DialogueStrategy
  onChange: (value: DialogueStrategy) => void
  disabled?: boolean
  disabledReason?: string
  rounds: number
}) {
  const reason = disabledReason ?? '任务正在执行，请等待完成或先停止任务再修改对白策略。'
  return <section className="card" aria-label="对白处理策略">
    <label className="ndr-field">对白处理策略
      <select data-testid="dialogue-strategy" value={value} disabled={disabled} {...disabledHint(disabled && reason)}
        onChange={event => onChange(event.target.value as DialogueStrategy)}>
        <option value="legacy">原有窗口流程</option>
        <option value="complete">完整上下文与简短归属（试验）</option>
        <option value="complete-review">完整上下文与独立复核裁决（试验）</option>
        <option value="complete-blocks">完整对话块分窗与简短归属（试验）</option>
        <option value="complete-blocks-review">完整对话块分窗与独立复核（试验）</option>
        <option value="complete-blocks-isolated">完整对话块分窗与辅助信息隔离（试验）</option>
        <option value="complete-blocks-isolated-review">完整对话块分窗、辅助隔离与独立复核（试验）</option>
        <option value="complete-blocks-isolated-feedback-review">完整对话块分窗、人物名单反馈与独立复核（试验）</option>
      </select>
    </label>
    <DisabledHint reason={disabled && reason} />
    <DisabledHint reason={dialogueStrategyDisabledReason(value, rounds)} />
    {value === 'complete-blocks-isolated-feedback-review' && <p className="hint">每窗额外调用一次人物名单反馈，可提出遗漏人物、身份关联或第一视角问题；反馈不是事实，会交由独立复核与裁决判断，不直接修改全书人物或第一视角。需要至少 1 次复核，额外消耗 Tokens；任务详情可查看反馈分类和理由。</p>}
    {value.includes('-isolated') && <p className="hint">模型可提供受话人物等辅助信息。无效辅助信息会单独隔离；不依赖它的有效归属保留，依赖它的归属转为待确认。主归属仍须通过完整校验；隔离本身不增加模型调用。</p>}
    <p className="hint">单章、批量、自动处理与局部复核共用此选择；只影响之后创建的对白任务，不改变人物分析、人工锁定或运行中任务。</p>
    {value !== 'legacy' && <p className="hint">保留所选范围的完整原文，不把不同章节拼接；完整范围过长会明确提示，不静默截断。可判断发声、心声和引用的人物。此策略仍在质量评估中，不保证更准确，也不会自动开启思考或提高输出上限。</p>}
    {value.startsWith('complete-blocks') && <p className="hint">整章能放下时一次处理；长章按完整段落和连续对话分窗，保留边界原文，不按固定对白条数切分。单个完整对话块仍过长时会提示，不截断对白。初读不使用窗口之后的原文。</p>}
    {value.endsWith('-review') && <p className="hint">{rounds > 0
      ? `最多 ${rounds} 轮独立复核，每轮检查全部目标；出现分歧时还可能调用裁决与核验，增加用量和等待。`
      : '当前复核次数为 0，独立复核已关闭；需要复核时请将每个窗口最多复核次数设为至少 1。'}争议保留待确认，不以投票替代证据。</p>}
  </section>
}
