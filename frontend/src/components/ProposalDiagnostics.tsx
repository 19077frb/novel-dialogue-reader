import { CollapsibleBlock } from './CollapsibleBlock'

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
  if (!identities && !facts && !descriptions) return null
  const details = Array.isArray(data.details) ? data.details.slice(0, 100).flatMap(item => {
    if (!item || typeof item !== 'object' || typeof item.message !== 'string') return []
    return [{ index: Number.isSafeInteger(item.character_index) ? item.character_index : '?',
      message: item.message.slice(0, 240) }]
  }) : []
  return <CollapsibleBlock title="人物分析提示" defaultOpen={false} summary={
    <p className="hint" role="status">
      {repaired
        ? <>初次隔离的 {identities} 个人物已完成定向修复；</>
        : <>已保留有效人物提案；隔离 {identities} 个人物，</>}
      移除 {facts} 条辅助信息和 {descriptions} 项无依据说明。
      请检查保留名单；遗漏人物可以手动添加，这些提案尚不等于人工确认。
    </p>
  }>
    {repaired && <p className="hint">以下为初次校验记录。人物提案已通过修复校验，仍需核对人物是否正确。</p>}
    {repairError && <p className="hint">人物修复未通过：{repairError}</p>}
    <ul>{details.map((item, index) => <li key={index}>第 {item.index} 个人物：{item.message}</li>)}</ul>
    {details.length === 100 && <p className="hint">仅展示前 100 条详情，以上数量包含全部隔离结果。</p>}
  </CollapsibleBlock>
}
