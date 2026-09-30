import type { BudgetInput } from '../api/jobs'

/**
 * 预算表单：把上限直接交给后端；留空表示不设该上限。
 * 表单本身不调用模型；使用它的页面可在预算改变后重新进行本地窗口估算。
 */
export interface BudgetFormProps {
  value: BudgetInput
  onChange: (value: BudgetInput) => void
}

function numberOrNull(raw: string): number | null {
  if (raw.trim() === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? Math.floor(parsed) : null
}

export function BudgetForm({ value, onChange }: BudgetFormProps) {
  return (
    <fieldset className="ndr-budget-form" data-testid="budget-form">
      <legend>预算与复核上限</legend>
      <label>
        输入 token 上限（留空=不限）
        <input
          type="number"
          min={1}
          value={value.maxInputTokens ?? ''}
          onChange={(event) => onChange({ ...value, maxInputTokens: numberOrNull(event.target.value) })}
          data-testid="budget-max-input"
        />
      </label>
      <label>
        输出 token 上限（留空=不限）
        <input
          type="number"
          min={1}
          value={value.maxOutputTokens ?? ''}
          onChange={(event) =>
            onChange({ ...value, maxOutputTokens: numberOrNull(event.target.value) })
          }
          data-testid="budget-max-output"
        />
      </label>
      <label>
        每个窗口最多复核的待定对白数
        <input
          type="number"
          min={0}
          value={value.maxRechecks}
          onChange={(event) =>
            onChange({ ...value, maxRechecks: Math.max(0, Number(event.target.value) || 0) })
          }
          data-testid="budget-max-rechecks"
        />
      </label>
    </fieldset>
  )
}
