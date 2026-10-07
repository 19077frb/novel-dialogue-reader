/**
 * Gap 更正：确认两个引语之间的叙述间隔意味着什么。
 *
 * 场景边界问题必须走 Gap 接口，不能误用说话人确认接口。
 */
import type { GapDecision, GapOut } from '../api/types'
import { gapDecisionLabel } from '../ui/labels'

const DECISIONS: { value: GapDecision; label: string; hint: string }[] = [
  { value: 'CONTINUE', label: '继续', hint: '同一场对话继续' },
  { value: 'UPDATE', label: '推进', hint: '场景内部推进，不切断' },
  { value: 'BREAK', label: '断开', hint: '这里结束当前场景，之后的引语归入新场景' },
  { value: 'UNCERTAIN', label: '待定', hint: '证据不足，留在待确认队列' },
]

export interface GapDecisionControlsProps {
  gap: GapOut
  busy?: boolean
  onDecide: (decision: GapDecision) => void
}

export function GapDecisionControls({ gap, busy = false, onDecide }: GapDecisionControlsProps) {
  return (
    <div className="ndr-gap-decision" data-testid="gap-decision">
      <p className="hint">场景间隔确认（不调用模型）：当前为{gapDecisionLabel(gap.decision)}</p>
      <ul className="ndr-candidates">
        {DECISIONS.map((item) => (
          <li key={item.value}>
            <button
              type="button"
              disabled={busy || gap.decision === item.value}
              title={busy ? '正在保存当前决定，请等待完成。' : gap.decision === item.value ? '当前已采用此决定，无需重复选择。' : undefined}
              onClick={() => onDecide(item.value)}
              data-testid={`gap-decision-${item.value}`}
            >
              {item.label}
            </button>
            <span className="hint">{item.hint}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
