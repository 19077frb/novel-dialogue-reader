import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { ProposalDiagnostics } from '../src/components/ProposalDiagnostics'

it('keeps isolation counts visible and lets the reader expand exact errors', () => {
  render(<ProposalDiagnostics value={{ isolated_characters: 1, discarded_auxiliary_facts: 2,
    discarded_descriptions: 1, details: [{ character_index: 2, message: '人物姓名缺少依据' }] }} />)
  expect(screen.getByRole('status')).toHaveTextContent('隔离 1 个人物，移除 2 条辅助信息和 1 项无依据说明')
  expect(screen.getByText(/人物姓名缺少依据/)).not.toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '展开人物分析提示' }))
  expect(screen.getByText(/第 2 个人物：人物姓名缺少依据/)).toBeVisible()
})

it('does not render old, malformed or empty diagnostic data', () => {
  const view = render(<ProposalDiagnostics value={undefined} />)
  expect(view.container).toBeEmptyDOMElement()
  for (const value of [null, [], 'error', { isolated_characters: '4' },
    { isolated_characters: -1 }, { isolated_characters: 0 }]) {
    view.rerender(<ProposalDiagnostics value={value} />)
    expect(view.container).toBeEmptyDOMElement()
  }
})

it('bounds optional details and does not expose arbitrary diagnostic objects', () => {
  render(<ProposalDiagnostics value={{ isolated_characters: 102,
    details: Array.from({ length: 102 }, (_, index) => ({
      character_index: index + 1, message: `错误 ${index + 1}`,
    })) }} />)
  expect(screen.getByRole('status')).toHaveTextContent('隔离 102 个人物')
  fireEvent.click(screen.getByRole('button', { name: '展开人物分析提示' }))
  expect(screen.getAllByRole('listitem')).toHaveLength(100)
  expect(screen.queryByText('第 101 个人物：错误 101')).not.toBeInTheDocument()
})

it('keeps partial repair visible without claiming all original identities are repaired', () => {
  render(<ProposalDiagnostics value={{ isolated_characters: 2, repair_succeeded: false,
    unresolved_groups: [[1]], repair_error: '部分身份仍未通过校验',
    repair_steps: [{ successful_groups: [[2]], unresolved_groups: [[1]], discarded_descriptions: 1,
      details: [{ diagnostics: { details: [{ message: '修复仍引用未来姓名' }] } }] }],
  }} />)
  expect(screen.getByRole('status')).toHaveTextContent('已修复 1 组，仍有 1 组未解决')
  expect(screen.queryByText(/已完成定向修复/)).not.toBeInTheDocument()
  expect(screen.getByText('修复仍引用未来姓名')).not.toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '展开人物分析提示' }))
  expect(screen.getByText('修复仍引用未来姓名')).toBeVisible()
  expect(screen.getByText('第 1 次人物修复')).toBeVisible()
})

it('bounds repair steps and treats malformed group data as zero rather than success', () => {
  render(<ProposalDiagnostics value={{ isolated_characters: 1,
    unresolved_groups: [[], [0], ['1'], null],
    repair_steps: Array.from({ length: 8 }, () => ({ successful_groups: [[], ['2'], [false]] })),
  }} />)
  expect(screen.getByRole('status')).toHaveTextContent('已修复 0 组，仍有 0 组未解决')
  fireEvent.click(screen.getByRole('button', { name: '展开人物分析提示' }))
  expect(screen.getAllByRole('heading', { level: 4 })).toHaveLength(5)
})
