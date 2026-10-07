import type { DialogueStrategy } from '../api/jobs'
import { dialogueStrategyDisabledReason } from '../api/jobs'
import { DisabledHint, disabledHint } from './DisabledHint'
import { CollapsibleBlock } from './CollapsibleBlock'

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
        <option value="legacy">分窗处理：精选上下文［默认·小窗口］</option>
        <optgroup label="完整对话块：整章优先，长章自动分窗（试验）">
        <option value="complete-blocks">对话块：简短归属［较省用量］</option>
        <option value="complete-blocks-review">对话块：独立复核［全窗复核］</option>
        <option value="complete-blocks-isolated">对话块：辅助信息隔离［不加复核］</option>
        <option value="complete-blocks-isolated-review">对话块：辅助隔离与独立复核{rounds > 0 ? '［推荐·质量优先］' : '［需开启复核］'}</option>
        <option value="complete-blocks-isolated-feedback-review">对话块：人物反馈与独立复核［额外人物检查］</option>
        </optgroup>
        <optgroup label="其他完整上下文方式（试验）">
        <option value="complete">完整上下文：简短归属［短范围·不分窗］</option>
        <option value="complete-review">完整上下文：独立复核裁决［短范围·不分窗］</option>
        </optgroup>
      </select>
    </label>
    <DisabledHint reason={disabled && reason} />
    <DisabledHint reason={dialogueStrategyDisabledReason(value, rounds)} />
    <p className="hint">怎么选：质量优先，建议选择“对话块：辅助隔离与独立复核”，并将复核次数设为至少 1。
      想减少附加调用，可选择“对话块：简短归属”。已有选择不会自动更改。</p>
    <p className="hint">推荐基于目前有限样本和流程能力，不代表所有书籍、模型上都最准确；模型支持时可配合高思考，需在思考设置中另行选择。
      “较省用量”仅指相同输入与模型下省去独立复核、人物反馈等附加调用，不保证总 Token 最少；重试、思考和章节长度仍会影响消耗。</p>
    <p className="hint" role="status">{value === 'legacy'
      ? '按较小窗口处理对白，使用预算内挑选的上下文。'
      : value.startsWith('complete-blocks')
        ? '整章能放下时一次处理；长章保留完整段落和连续对白，分窗处理。'
        : '保留所选范围的连续原文；范围超过预算时需要缩小范围。'}
      {value !== 'legacy' && ' 试验策略不保证更准确，也不会自动开启模型思考。'}
      {value.includes('-isolated') && ' 无效辅助信息单独隔离，主归属仍需通过校验。'}
      {value.endsWith('-review') && (rounds > 0 ? ` 最多 ${rounds} 轮复核，裁决与核验可能额外调用模型并消耗 Tokens。` : ' 复核次数为 0，独立复核已关闭。')}
      {value === 'complete-blocks-isolated-feedback-review' && ' 人物反馈每窗额外调用一次模型，目前未证明有稳定质量收益。'}
    </p>
    <CollapsibleBlock title="策略区别与使用说明" defaultOpen={false}>
    {value === 'complete-blocks-isolated-feedback-review' && <p className="hint">每窗额外调用一次人物名单反馈，可提出遗漏人物、身份关联或第一视角问题；反馈不是事实，会交由独立复核与裁决判断，不直接修改全书人物或第一视角。需要至少 1 次复核，额外消耗 Tokens；任务详情可查看反馈分类和理由。</p>}
    {value.includes('-isolated') && <p className="hint">模型可提供受话人物等辅助信息。无效辅助信息会单独隔离；不依赖它的有效归属保留，依赖它的归属转为待确认。主归属仍须通过完整校验；隔离本身不增加模型调用。</p>}
    <p className="hint">单章、批量、自动处理与局部复核共用此选择；只影响之后创建的对白任务，不改变人物分析、人工锁定或运行中任务。</p>
    {value !== 'legacy' && <p className="hint">保留所选范围的完整原文，不把不同章节拼接；完整范围过长会明确提示，不静默截断。可判断发声、心声和引用的人物。此策略仍在质量评估中，不保证更准确，也不会自动开启思考或提高输出上限。</p>}
    {value.startsWith('complete-blocks') && <p className="hint">整章能放下时一次处理；长章按完整段落和连续对话分窗，保留边界原文，不按固定对白条数切分。单个完整对话块仍过长时会提示，不截断对白。初读不使用窗口之后的原文。</p>}
    {value.endsWith('-review') && <p className="hint">{rounds > 0
      ? `最多 ${rounds} 轮独立复核，每轮检查全部目标；出现分歧时还可能调用裁决与核验，增加用量和等待。`
      : '当前复核次数为 0，独立复核已关闭；需要复核时请将每个窗口最多复核次数设为至少 1。'}争议保留待确认，不以投票替代证据。</p>}
    </CollapsibleBlock>
  </section>
}
