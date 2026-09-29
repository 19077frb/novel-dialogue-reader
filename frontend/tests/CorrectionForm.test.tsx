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
      kind: null,
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
