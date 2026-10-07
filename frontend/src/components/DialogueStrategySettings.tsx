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
        <option value="complete-blocks-isolated">对话块：辅助信息隔离［辅助容错］</option>
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
    <p className="hint">拿不准时，选带“推荐”标签的方案，并把复核次数设为至少 1。想减少用量，选“较省用量”方案。</p>
    <p className="hint">复核次数越多、思考越深入，通常用量和等待也会增加。常规复核按你设置的次数执行。</p>
    <p className="hint" role="status">{value === 'legacy'
      ? '每次处理一小段对白，并参考附近的原文。'
      : value.startsWith('complete-blocks')
        ? '结合完整原文判断说话人；长章按完整段落和连续对白分窗处理。'
        : '一次处理所选范围的完整原文；范围太长时，请缩小处理范围。'}
      {value.includes('-isolated') && ' 辅助信息有问题时，保留不受影响的对白结果。'}
      {value.endsWith('-review') && (rounds > 0 ? ` 最多复核 ${rounds} 次；有分歧时会额外调用模型检查，增加用量和等待。` : ' 复核次数为 0，独立复核已关闭。')}
      {value === 'complete-blocks-isolated-feedback-review' && ' 每窗额外调用一次模型，检查是否漏掉人物或认错身份。'}
    </p>
    <CollapsibleBlock title="策略区别与使用说明" defaultOpen={false}>
    {value === 'complete-blocks-isolated-feedback-review' && <p className="hint">适合怀疑人物名单有遗漏时使用。每窗多调用一次模型，并配合至少 1 次复核。人物建议和理由可在任务详情查看；需要修改第一视角主人公时，请回到本章人物确认。</p>}
    {value.includes('-isolated') && <p className="hint">例如模型没判断清楚对白是在对谁说，不受影响的对白结果会保留；需要核对的相关对白会放入待确认队列。</p>}
    <p className="hint">单章、批量、自动处理与局部复核共用此选择。修改后从新任务开始生效，正在运行的任务保持原设置。</p>
    {value !== 'legacy' && <p className="hint">模型结合原文判断对白、心声和引用属于谁。需要更细致的判断时，可在思考设置中选择模型支持的思考强度。</p>}
    {value.startsWith('complete-blocks') && <p className="hint">整章能放下时一次处理，长章自动分窗。如果一个完整段落仍太长，请缩小处理范围。初读只参考当前窗口及前文，重读可参考处理范围内的后文。</p>}
    {value.endsWith('-review') && <p className="hint">{rounds > 0
      ? `最多复核 ${rounds} 次，每次检查当前窗口的全部对白；有分歧时会再调用模型检查。`
      : '独立复核已关闭；要开启，请将每个窗口最多复核次数设为至少 1。'}仍无法判断的对白会留在待确认队列。</p>}
    </CollapsibleBlock>
  </section>
}
