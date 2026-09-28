import type { UsageOut } from '../api/types'

/**
 * 用量汇总：直接显示后端结算结果。
 * 未知用量的尝试单独计数，绝不按 0 计入 token；缺价格资料时不显示金额。
 */
export function UsageSummary({ usage }: { usage: UsageOut }) {
  return (
    <div className="ndr-usage" data-testid="usage-summary">
      <dl>
        <dt>尝试次数</dt>
        <dd data-testid="usage-runs">{usage.runs}</dd>
        <dt>未知用量</dt>
        <dd data-testid="usage-unknown">{usage.unknown_usage_runs}</dd>
        <dt>输入 token</dt>
        <dd>{usage.input_tokens}</dd>
        <dt>输出 token</dt>
        <dd>{usage.output_tokens}</dd>
        <dt>合计 token</dt>
        <dd data-testid="usage-total">{usage.total_tokens}</dd>
        <dt>金额</dt>
        <dd data-testid="usage-cost">
          {usage.cost && usage.currency ? `${usage.cost} ${usage.currency}` : '缺价格资料，不显示金额'}
        </dd>
      </dl>
      {usage.unknown_usage_runs > 0 && (
        <p className="hint" data-testid="usage-unknown-warning">
          有 {usage.unknown_usage_runs} 次尝试未返回 usage，已单独计数，<strong>没有</strong>按 0 计入 token。
        </p>
      )}
    </div>
  )
}