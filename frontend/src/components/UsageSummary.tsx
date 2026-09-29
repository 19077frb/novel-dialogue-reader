import type { JobDetailOut, UsageOut } from '../api/types'

/**
 * 本次任务与本书累计用量。未知用量的尝试单独计数，绝不按 0 计入 token。
 */
export function UsageSummary({
  usage,
  currentJob,
}: {
  usage: UsageOut
  currentJob?: JobDetailOut | null
}) {
  const currentInput = Number(currentJob?.usage?.input_tokens ?? 0)
  const currentOutput = Number(currentJob?.usage?.output_tokens ?? 0)

  return (
    <div className="ndr-usage" data-testid="usage-summary">
      {currentJob && (
        <>
          <h3>本次任务消耗</h3>
          <dl data-testid="usage-current">
            <dt>尝试次数</dt>
            <dd>{currentJob.calls}</dd>
            <dt>未知用量</dt>
            <dd>{currentJob.unknown_usage_runs}</dd>
            <dt>输入 token</dt>
            <dd>{currentInput}</dd>
            <dt>输出 token</dt>
            <dd>{currentOutput}</dd>
            <dt>合计 token</dt>
            <dd data-testid="usage-current-total">{currentInput + currentOutput}</dd>
          </dl>
        </>
      )}
      <h3>本书累计消耗</h3>
      <dl data-testid="usage-cumulative">
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
      </dl>
      {usage.unknown_usage_runs > 0 && (
        <p className="hint" data-testid="usage-unknown-warning">
          有 {usage.unknown_usage_runs} 次尝试未返回 usage，已单独计数，<strong>没有</strong>按 0 计入 token。
        </p>
      )}
    </div>
  )
}
