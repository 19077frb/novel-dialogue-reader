import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { CorrectionForm } from '../src/components/CorrectionForm'
import type { AnnotationStateOut, SceneGroupRefOut } from '../src/api/types'

const ANNOTATION = {
  quote_id: 'q1',
  scene_id: 's1',
  kind: 'speech',
  assignment: 'EXISTING',
  basis: 'DIRECT',
  speaker_group_id: 'g1',
  label: 'S1',
  status: 'ACCEPTED',
  source: 'MODEL',
  stale: false,
  user_locked: false,
  visible_from_cp: 10,
  version: 4,
} as AnnotationStateOut

const GROUPS: SceneGroupRefOut[] = [
  { group_id: 'g1', label: 'S1', canonical_name: '浅村悠太', description: '本章主人公' },
  { group_id: 'g2', label: 'S2', canonical_name: '绫濑沙季', description: '主人公的义妹' },
  { group_id: 'g3', label: 'S3', canonical_name: null, description: '后排的男同学' },
]

describe('CorrectionForm', () => {
  it('术语可改为非人物文本，不要求选择人物', async () => {
    const onSubmit = vi.fn()
    render(<CorrectionForm annotation={ANNOTATION} sceneGroups={GROUPS} sceneVersion={1} onSubmit={onSubmit} />)
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'set_kind')
    await userEvent.selectOptions(screen.getByTestId('correction-kind'), 'other')
    expect(screen.getByRole('option', { name: '非人物文本（无需归属）' })).toBeInTheDocument()
    await userEvent.click(screen.getByTestId('correction-submit'))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ action: 'set_kind', kind: 'other' }))
  })
  it('显示当前版本，并把动作与期望版本一起提交（不调用模型）', async () => {
    const onSubmit = vi.fn()
    render(
      <CorrectionForm
        annotation={ANNOTATION}
        sceneGroups={GROUPS}
        sceneVersion={2}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByTestId('correction-version')).toHaveTextContent('版本 4')
    expect(screen.getByRole('option', { name: '浅村悠太' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: '绫濑沙季' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'S1' })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: '后排的男同学' })).toHaveAttribute(
      'title',
      '后排的男同学',
    )
    expect(screen.getByRole('option', { name: '绫濑沙季' })).toHaveAttribute(
      'title',
      '主人公的义妹',
    )
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'assign_existing')
    await userEvent.selectOptions(screen.getByTestId('correction-speaker'), 'g2')
    await userEvent.click(screen.getByTestId('correction-submit'))

    expect(onSubmit).toHaveBeenCalledWith({
      action: 'assign_existing',
      speakerRef: 'g2',
      kind: 'speech',
      description: '',
      expectedVersion: 4,
      expectedSceneVersion: null,
      note: 'user-correction',
    })
  })

  it('新建说话人带说明，并提交场景期望版本', async () => {
    const onSubmit = vi.fn()
    render(
      <CorrectionForm
        annotation={ANNOTATION}
        sceneGroups={GROUPS}
        sceneVersion={3}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'create_speaker')
    await userEvent.type(screen.getByTestId('correction-description'), '第二个声音')
    await userEvent.click(screen.getByTestId('correction-submit'))

    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({
        action: 'create_speaker',
        description: '第二个声音',
        expectedSceneVersion: 3,
      }),
    )
  })

  it('空候选（场景内没有已有分组）时默认新建，并说明原因', async () => {
    const onSubmit = vi.fn()
    render(
      <CorrectionForm
        annotation={null}
        sceneGroups={[]}
        sceneVersion={1}
        onSubmit={onSubmit}
      />,
    )

    expect(screen.getByTestId('correction-no-groups')).toBeInTheDocument()
    expect(screen.queryByTestId('correction-speaker')).toBeNull()
    const action = screen.getByTestId('correction-action') as HTMLSelectElement
    expect(action.value).toBe('create_speaker')
    // 没有已有分组时不应提供「指定已有说话人」
    expect(Array.from(action.options).map((option) => option.value)).not.toContain(
      'assign_existing',
    )

    await userEvent.click(screen.getByTestId('correction-submit'))
    expect(onSubmit).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'create_speaker', expectedVersion: null }),
    )
  })

  it('修改类型必须带 kind', async () => {
    const onSubmit = vi.fn()
    render(
      <CorrectionForm
        annotation={ANNOTATION}
        sceneGroups={GROUPS}
        sceneVersion={1}
        onSubmit={onSubmit}
      />,
    )

    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'set_kind')
    await userEvent.selectOptions(screen.getByTestId('correction-kind'), 'thought')
    await userEvent.click(screen.getByTestId('correction-submit'))

    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ action: 'set_kind', kind: 'thought' }))
  })

  it.each(['thought', 'quotation'] as const)('指定人物保留当前%s类型，可单独改类型', async (kind) => {
    const onSubmit = vi.fn()
    render(<CorrectionForm annotation={{ ...ANNOTATION, kind }} sceneGroups={GROUPS}
      sceneVersion={1} onSubmit={onSubmit} />)
    expect(screen.getByTestId('correction-owner-kind')).toHaveValue(kind)
    await userEvent.selectOptions(screen.getByTestId('correction-speaker'), 'g2')
    await userEvent.click(screen.getByTestId('correction-submit'))
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
      action: 'assign_existing', kind, speakerRef: 'g2',
    }))
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'create_speaker')
    await userEvent.selectOptions(screen.getByTestId('correction-owner-kind'), 'speech')
    await userEvent.click(screen.getByTestId('correction-submit'))
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({
      action: 'create_speaker', kind: 'speech',
    }))
  })

  it('集体类型指定个人默认发声，切句重置类型，同句刷新保留未保存选择', async () => {
    const props = { sceneGroups: GROUPS, sceneVersion: 1, onSubmit: vi.fn() }
    const { rerender } = render(<CorrectionForm {...props}
      annotation={{ ...ANNOTATION, kind: 'group' }} />)
    expect(screen.getByTestId('correction-owner-kind')).toHaveValue('speech')
    await userEvent.selectOptions(screen.getByTestId('correction-owner-kind'), 'quotation')
    rerender(<CorrectionForm {...props} annotation={{ ...ANNOTATION, kind: 'speech', version: 5 }} />)
    expect(screen.getByTestId('correction-owner-kind')).toHaveValue('quotation')
    rerender(<CorrectionForm {...props} annotation={{ ...ANNOTATION, quote_id: 'q2', kind: 'thought' }} />)
    expect(screen.getByTestId('correction-owner-kind')).toHaveValue('thought')
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'set_kind')
    expect(screen.getByTestId('correction-kind')).toHaveValue('thought')
    expect(screen.getByText(/会保留已有归属/)).toBeInTheDocument()
  })

  it('同句已保存新类型后，未编辑的指定人物类型跟随更新，不退回发声', async () => {
    const onSubmit = vi.fn()
    const props = { sceneGroups: GROUPS, sceneVersion: 1, onSubmit }
    const { rerender } = render(<CorrectionForm {...props} annotation={ANNOTATION} />)
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'set_kind')
    await userEvent.selectOptions(screen.getByTestId('correction-kind'), 'thought')
    await userEvent.click(screen.getByTestId('correction-submit'))
    rerender(<CorrectionForm {...props} annotation={{ ...ANNOTATION, kind: 'thought', version: 5 }} />)
    await userEvent.selectOptions(screen.getByTestId('correction-action'), 'assign_existing')
    expect(screen.getByTestId('correction-owner-kind')).toHaveValue('thought')
    await userEvent.click(screen.getByTestId('correction-submit'))
    expect(onSubmit).toHaveBeenLastCalledWith(expect.objectContaining({ kind: 'thought', expectedVersion: 5 }))
  })

  it('提交中禁用提交按钮（不会重复提交）', () => {
    render(
      <CorrectionForm
        annotation={ANNOTATION}
        sceneGroups={GROUPS}
        sceneVersion={1}
        busy
        onSubmit={vi.fn()}
      />,
    )
    expect(screen.getByTestId('correction-submit')).toBeDisabled()
  })
})
