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
