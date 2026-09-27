import type { EstimateOut } from '../api/types'

/** 估算结果（纯本地启发式，不代表真实计费）。 */
export function EstimateSummary({ estimate }: { estimate: EstimateOut }) {
  return (
    <div className="ndr-estimate" data-testid="estimate-summary">
      <dl>
        <dt>处理窗口</dt>
        <dd data-testid="estimate-windows">{estimate.window_count}</dd>
        <dt>目标对白</dt>
        <dd data-testid="estimate-targets">{estimate.target_count}</dd>
        <dt>预计输入</dt>
        <dd>{estimate.input_tokens} token</dd>
        <dt>预计输出</dt>
        <dd>{estimate.output_tokens} token</dd>
        <dt>合计</dt>
        <dd data-testid="estimate-total">{estimate.total_tokens} token</dd>
      </dl>
      <p className="hint">估算来自本地启发式 token 口径，不是真实计费依据；实际用量以提供方 usage 为准。</p>
      {(estimate.notes ?? []).length > 0 && (
        <ul className="hint">
          {(estimate.notes ?? []).map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </div>
  )
}