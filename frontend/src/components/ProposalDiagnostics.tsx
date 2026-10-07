import { CollapsibleBlock } from './CollapsibleBlock'

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

function groups(value: unknown): number {
  return Array.isArray(value) ? value.filter(group => Array.isArray(group) && group.length > 0
    && group.every(index => Number.isSafeInteger(index) && index > 0)).length : 0
}

function repairMessages(step: Record<string, unknown>): string[] {
  if (!Array.isArray(step.details)) return []
  return step.details.slice(0, 100).flatMap(value => {
    const detail = record(value)
    const diagnostic = record(detail?.diagnostics)
    return Array.isArray(diagnostic?.details) ? diagnostic.details.slice(0, 100).flatMap(value => {
      const item = record(value)
      return typeof item?.message === 'string' ? [item.message.slice(0, 240)] : []
    }) : []
  }).slice(0, 100)
}

/** Progress from old or future servers is unknown data, not trusted UI state. */
export function ProposalDiagnostics({ value }: { value: unknown }) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const data = value as Record<string, unknown>
  const count = (key: string) => typeof data[key] === 'number'
    && Number.isSafeInteger(data[key]) && (data[key] as number) >= 0 ? data[key] as number : 0
  const identities = count('isolated_characters')
  const facts = count('discarded_auxiliary_facts')
  const descriptions = count('discarded_descriptions')
  const repaired = data.repair_succeeded === true
  const repairError = typeof data.repair_error === 'string' ? data.repair_error.slice(0, 400) : ''
  const steps = Array.isArray(data.repair_steps) ? data.repair_steps.slice(0, 5).flatMap(value => {
    const step = record(value)
    return step ? [step] : []
  }) : []
  const successful = steps.reduce((total, step) => total + groups(step.successful_groups), 0)
  const pending = groups(data.unresolved_groups)
  if (!identities && !facts && !descriptions) return null
  const details = Array.isArray(data.details) ? data.details.slice(0, 100).flatMap(item => {
    if (!item || typeof item !== 'object' || typeof item.message !== 'string') return []
    return [{ index: Number.isSafeInteger(item.character_index) ? item.character_index : '?',
      message: item.message.slice(0, 240) }]
  }) : []
  return <CollapsibleBlock title="人物分析提示" defaultOpen={false} summary={
    <p className="hint" role="status">
      {steps.length > 0
        ? <>初次隔离 {identities} 个人物；已修复 {successful} 组，仍有 {pending} 组未解决。初次校验</>
        : repaired
        ? <>初次隔离的 {identities} 个人物已完成定向修复；</>
        : <>已保留有效人物提案；隔离 {identities} 个人物，</>}
      移除 {facts} 条辅助信息和 {descriptions} 项无依据说明。
      请检查保留名单；遗漏人物可以手动添加，这些提案尚不等于人工确认。
    </p>
  }>
    {repaired && <p className="hint">以下为初次校验记录。人物提案已通过修复校验，仍需核对人物是否正确。</p>}
    {repairError && <p className="hint">人物修复未通过：{repairError}</p>}
    <ul>{details.map((item, index) => <li key={index}>第 {item.index} 个人物：{item.message}</li>)}</ul>
    {steps.map((step, index) => <section key={index}>
      <h4>第 {index + 1} 次人物修复</h4>
      <p className="hint">修复 {groups(step.successful_groups)} 组，剩余 {groups(step.unresolved_groups)} 组；
        {typeof step.discarded_descriptions === 'number' && Number.isSafeInteger(step.discarded_descriptions)
          && step.discarded_descriptions >= 0 ? `移除 ${step.discarded_descriptions} 项无依据说明。` : ''}
      </p>
      <ul>{repairMessages(step).map((message, index) => <li key={index}>{message}</li>)}</ul>
    </section>)}
    {details.length === 100 && <p className="hint">仅展示前 100 条详情，以上数量包含全部隔离结果。</p>}
  </CollapsibleBlock>
}
