/**
 * 对白上下文：只展示后端返回的原文片段。
 *
 * 「展开更多原文」= 重新请求更大 `context_window_cp` 的本地原文，**不调用模型**；
 * 与「模型复核」是完全不同的操作。
 */
export interface QuoteContextProps {
  before: string
  text: string
  after: string
  contextWindowCp: number
  onExpand: () => void
}

export function QuoteContext({
  before,
  text,
  after,
  contextWindowCp,
  onExpand,
}: QuoteContextProps) {
  return (
    <div className="ndr-quote-context" data-testid="quote-context">
      <p className="hint">
        前后各 {contextWindowCp} 码点的原文（只读本地原文，不调用模型）
      </p>
      <p className="ndr-context-line">
        {before}
        <mark className="ndr-context-target" data-testid="quote-context-target">
          {text}
        </mark>
        {after}
      </p>
      <button type="button" onClick={onExpand} data-testid="expand-context">
        展开更多原文
      </button>
    </div>
  )
}