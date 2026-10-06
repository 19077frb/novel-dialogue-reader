import { CollapsibleBlock } from './CollapsibleBlock'

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

const labels: Record<string, string> = {
  omitted_identity: '可能遗漏人物', incorrect_association: '人物关联有争议', incorrect_pov: '第一视角建议',
}

/** Read only the already-polled checkpoint; feedback is not an accepted character fact. */
export function IdentityFeedbackSummary({ checkpoint }: { checkpoint: unknown }) {
  const reviews = object(object(checkpoint)?.expression_reviews)
  if (!reviews) return null
  const entries = Object.entries(reviews).flatMap(([id, value]) => {
    const feedback = object(object(value)?.identity_feedback)
    if (!feedback || !Array.isArray(feedback.issues)) return []
    return [{ id, issues: feedback.issues.map(object).filter(issue => issue !== null) }]
  })
  if (!entries.length) return null
  const count = entries.reduce((total, entry) => total + entry.issues.length, 0)
  return <CollapsibleBlock title="人物名单反馈" defaultOpen={false}
    summary={`${entries.length} 个窗口已收到反馈，提出 ${count} 个问题；反馈不是最终确认结果。`}>
    <p className="hint">归属建议仍需复核与裁决，争议会保留待确认。第一视角建议不会自动修改本章主人公；可回到本章人物确认查看原文后调整。</p>
    {entries.slice(-20).map((entry, index) => <section key={entry.id}>
      <h4>窗口反馈 {Math.max(0, entries.length - 20) + index + 1}</h4>
      {entry.issues.length === 0 ? <p className="hint">本次反馈没有提出名单问题，不代表已经人工核实。</p>
        : <ul className="hint">{entry.issues.slice(0, 100).map((issue, n) => <li key={n}>
          {labels[String(issue.kind)] ?? '人物问题'}：{typeof issue.reason === 'string' ? issue.reason : '未提供说明'}
          {Array.isArray(issue.targets) && `（涉及 ${issue.targets.length} 句）`}
        </li>)}</ul>}
      {entry.issues.length > 100 && <p className="hint">这里只显示前 100 个问题；完整记录仍随任务保留。</p>}
    </section>)}
    {entries.length > 20 && <p className="hint">这里只显示最近 20 个窗口的反馈。</p>}
  </CollapsibleBlock>
}
