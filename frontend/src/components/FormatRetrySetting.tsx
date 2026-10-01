export function FormatRetrySetting({ value, onChange, disabled, testId = 'budget-format-retries' }: {
  value: number; onChange: (value: number) => void; disabled?: boolean; testId?: string
}) {
  return <label className="ndr-field">
    校验失败后的重试次数
    <input type="number" min={0} max={5} step={1} value={value} disabled={disabled}
      data-testid={testId} onChange={event => onChange(Math.min(5, Math.max(0, Math.floor(Number(event.target.value) || 0))))} />
    <span className="hint">0 不重试，默认 1，最多 5 次，不含首次调用。仅对白输出校验失败时纠错重发；每次会消耗 Token，超时不自动重发。</span>
  </label>
}
