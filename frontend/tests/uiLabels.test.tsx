import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DialogueStrategySettings } from '../src/components/DialogueStrategySettings'
import { annotationStatusLabel, credentialLabel, protocolLabel, quoteKindLabel, reviewReasonLabel, queueStatusLabel, correctionActionLabel } from '../src/ui/labels'

describe('Chinese display labels and processing choices', () => {
  it('translates known values without inventing a meaning for unknown identifiers', () => {
    expect(protocolLabel('chat-completions-compatible')).toBe('兼容聊天接口')
    expect(credentialLabel('session')).toBe('仅本次会话')
    expect(annotationStatusLabel('USER_CONFIRMED')).toBe('已人工确认')
    expect(quoteKindLabel('quotation')).toBe('引用')
    expect(reviewReasonLabel('LOW_CONFIDENCE')).toBe('置信度低')
    expect(queueStatusLabel('PENDING')).toBe('待确认')
    expect(correctionActionLabel('assign_existing')).toBe('指定已有说话人')
    for (const label of [protocolLabel, credentialLabel, annotationStatusLabel, quoteKindLabel, reviewReasonLabel, queueStatusLabel, correctionActionLabel]) {
      expect(label('future-identifier')).toBe('future-identifier')
    }
  })
  it('groups all existing strategies without changing values or hiding costs and locks', () => {
    const change = vi.fn()
    const { rerender } = render(<DialogueStrategySettings value="complete-blocks-isolated-review" onChange={change} rounds={1} />)
    const select = screen.getByTestId('dialogue-strategy')
    expect(select.querySelectorAll('option')).toHaveLength(8)
    expect(select.querySelectorAll('optgroup')).toHaveLength(2)
    expect(screen.getByRole('status')).toHaveTextContent('额外调用模型')
    expect(screen.getByRole('button', { name: '展开策略区别与使用说明' })).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(screen.getByRole('button', { name: '展开策略区别与使用说明' }))
    fireEvent.change(select, { target: { value: 'legacy' } })
    expect(change).toHaveBeenCalledWith('legacy')
    rerender(<DialogueStrategySettings value="complete-blocks-isolated-feedback-review" onChange={change} rounds={0} disabled disabledReason="等待当前任务结束" />)
    expect(select).toBeDisabled()
    expect(screen.getByText('等待当前任务结束')).toBeVisible()
    expect(screen.getByText(/人物名单反馈需要独立复核/)).toBeVisible()
  })
  it('explains a bounded recommendation and savings without changing existing choices', () => {
    const change = vi.fn()
    const { rerender } = render(<DialogueStrategySettings value="legacy" onChange={change} rounds={1} />)
    const select = screen.getByTestId('dialogue-strategy')
    expect(select).toHaveValue('legacy')
    expect(screen.queryByText('原有窗口流程')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /默认·小窗口/ })).toHaveValue('legacy')
    expect(screen.getByRole('option', { name: /推荐·质量优先/ })).toHaveValue('complete-blocks-isolated-review')
    expect(screen.getByRole('option', { name: /较省用量/ })).toHaveValue('complete-blocks')
    expect(screen.getByText(/怎么选：质量优先/)).toBeVisible()
    expect(screen.getByText(/不保证总 Token 最少/)).toBeVisible()
    expect(screen.getByText(/常规复核仍按复核次数执行/)).toBeVisible()
    expect(screen.getByRole('option', { name: /辅助容错/ })).toHaveValue('complete-blocks-isolated')
    expect(screen.getByText(/目前有限样本/)).toBeVisible()
    expect(change).not.toHaveBeenCalled()
    expect([...select.querySelectorAll('option')].map(option => option.value)).toEqual([
      'legacy', 'complete-blocks', 'complete-blocks-review', 'complete-blocks-isolated',
      'complete-blocks-isolated-review', 'complete-blocks-isolated-feedback-review', 'complete', 'complete-review',
    ])
    rerender(<DialogueStrategySettings value="complete-blocks-isolated-review" onChange={change} rounds={0} />)
    expect(select).toHaveValue('complete-blocks-isolated-review')
    expect(screen.queryByRole('option', { name: /推荐·质量优先/ })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /需开启复核/ })).toHaveValue('complete-blocks-isolated-review')
    expect(screen.getByRole('status')).toHaveTextContent('独立复核已关闭')
    expect(change).not.toHaveBeenCalled()
  })
})
