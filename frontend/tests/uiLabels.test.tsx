import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { DialogueStrategySettings } from '../src/components/DialogueStrategySettings'
import { annotationSpeakerName, annotationStatusLabel, credentialLabel, gapDecisionLabel, protocolLabel, quoteKindLabel, reviewReasonLabel, queueStatusLabel, correctionActionLabel, sceneSpeakerName } from '../src/ui/labels'
import type { AnnotationStateOut } from '../src/api/types'
import type { GapOut } from '../src/api/types'
import { GapDecisionControls } from '../src/components/GapDecisionControls'

it.each(['CONTINUE', 'UPDATE', 'BREAK', 'UNCERTAIN'] as const)('场景确认显示中文含义，提交仍保留%s', decision => {
  const onDecide = vi.fn()
  render(<GapDecisionControls gap={{ decision } as GapOut} onDecide={onDecide} />)
  const controls = screen.getByTestId('gap-decision')
  expect(controls).toHaveTextContent(`当前为${gapDecisionLabel(decision)}`)
  expect(controls).not.toHaveTextContent(decision)
  const next = decision === 'BREAK' ? 'CONTINUE' : 'BREAK'
  fireEvent.click(screen.getByTestId(`gap-decision-${next}`))
  expect(onDecide).toHaveBeenCalledWith(next)
})

it('人物显示隐藏内部引用，非人物和集体表达显示真实含义', () => {
  const annotation = { label: 'S1', speaker_group_id: null, kind: 'speech' } as AnnotationStateOut
  expect(annotationSpeakerName(annotation, [])).toBe('尚未确定人物')
  expect(annotationSpeakerName({ ...annotation, label: '绫濑沙季' }, [])).toBe('绫濑沙季')
  expect(annotationSpeakerName({ ...annotation, kind: 'other' }, [])).toBe('无需归属')
  expect(annotationSpeakerName({ ...annotation, kind: 'group' }, [])).toBe('集体声音')
  for (const canonical_name of ['S1', 'C2', 'speaker_3', '06454cb8-f2d8-bb27-24a6-592d7feb1aa1']) {
    expect(sceneSpeakerName({ group_id: 'g1', label: 'S1', canonical_name, description: '' })).toBe('尚未确定人物')
  }
  expect(gapDecisionLabel('CONTINUE')).toBe('同一场对话继续')
  expect(gapDecisionLabel('UPDATE')).toBe('场景内部推进')
  expect(gapDecisionLabel('BREAK')).toBe('切换场景')
  expect(gapDecisionLabel('UNCERTAIN')).toBe('场景待确认')
})

describe('Chinese display labels and processing choices', () => {
  it('translates known values without inventing a meaning for unknown identifiers', () => {
    expect(protocolLabel('chat-completions-compatible')).toBe('兼容聊天接口')
    expect(credentialLabel('session')).toBe('仅本次会话')
    expect(annotationStatusLabel('USER_CONFIRMED')).toBe('已人工确认')
    expect(quoteKindLabel('quotation')).toBe('人物原话引用')
    expect(quoteKindLabel('other')).toBe('非人物文本（无需归属）')
    expect(reviewReasonLabel('UNKNOWN_QUOTE_SOURCE')).toBe('引用来源待确认')
    expect(reviewReasonLabel('UNKNOWN_QUOTE_KIND')).toBe('表达类型待确认')
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
  it('gives plain selection guidance without changing existing choices', () => {
    const change = vi.fn()
    const { rerender } = render(<DialogueStrategySettings value="legacy" onChange={change} rounds={1} />)
    const select = screen.getByTestId('dialogue-strategy')
    expect(select).toHaveValue('legacy')
    expect(screen.queryByText('原有窗口流程')).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /默认·小窗口/ })).toHaveValue('legacy')
    expect(screen.getByRole('option', { name: /推荐·质量优先/ })).toHaveValue('complete-blocks-isolated-review')
    expect(screen.getByRole('option', { name: /较省用量/ })).toHaveValue('complete-blocks')
    expect(screen.getByText(/拿不准时/)).toBeVisible()
    expect(screen.getByText(/用量和等待也会增加/)).toBeVisible()
    expect(screen.getByText(/常规复核按你设置的次数执行/)).toBeVisible()
    expect(screen.getByRole('option', { name: /辅助容错/ })).toHaveValue('complete-blocks-isolated')
    expect(screen.queryByText(/有限样本|不保证|未证明/)).not.toBeInTheDocument()
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
