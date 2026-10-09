import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { FilterableTaskProgressTable } from '../src/components/TaskProgressTable'
import type { TaskProgressRow } from '../src/components/TaskProgressTable'

const rows: TaskProgressRow[] = Array.from({ length: 44 }, (_, i) => ({
  id: `task-${i}`, state: i < 22 ? 'completed' : 'failed', stateLabel: i < 22 ? '已完成' : '失败',
  type: '对白归属', chapter: `章节 ${i}`, window: '窗口 1', actions: <button>查看任务</button>,
}))

it('filters the complete range across pages and resets paging when changing status', async () => {
  render(<FilterableTaskProgressTable label="范围内任务" rows={rows} scope="range" />)
  const table = screen.getByRole('table')
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(within(table).getByText('章节 20')).toBeVisible()
  await userEvent.selectOptions(screen.getByLabelText('筛选任务状态'), 'failed')
  expect(screen.getByRole('status')).toHaveTextContent('显示 22 / 44 项')
  expect(screen.getByRole('option', { name: '失败（22）' })).toBeInTheDocument()
  expect(within(table).queryByText('章节 20')).not.toBeInTheDocument()
  expect(within(table).getByText('章节 22')).toBeVisible()
  expect(within(table).getAllByRole('button', { name: '查看任务' })).toHaveLength(20)
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(within(table).getByText('章节 42')).toBeVisible()
  await userEvent.selectOptions(screen.getByLabelText('筛选任务状态'), 'completed')
  expect(within(table).getByText('章节 0')).toBeVisible()
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
  await userEvent.selectOptions(screen.getByLabelText('筛选任务状态'), 'running')
  expect(screen.getByText('没有“处理中”状态的任务。')).toBeVisible()
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
})

it('keeps the selected filter during progress updates and corrects the page after tasks leave it', async () => {
  const view = render(<FilterableTaskProgressTable label="范围内任务" rows={rows} scope="range" />)
  await userEvent.selectOptions(screen.getByLabelText('筛选任务状态'), 'failed')
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  view.rerender(<FilterableTaskProgressTable label="范围内任务" rows={rows.map((row, i) => i > 23
    ? { ...row, state: 'completed', stateLabel: '已完成' } : row)} scope="range" />)
  expect(screen.getByLabelText('筛选任务状态')).toHaveValue('failed')
  expect(screen.getByRole('status')).toHaveTextContent('显示 2 / 44 项')
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  expect(screen.getByText('章节 22')).toBeVisible()
  expect(screen.getByText('章节 23')).toBeVisible()
  view.rerender(<FilterableTaskProgressTable label="范围内任务" rows={rows} scope="another-range" />)
  expect(screen.getByLabelText('筛选任务状态')).toHaveValue('all')
})

it('keeps different ranges independent and supports every range task state', async () => {
  const states = ['queued', 'running', 'completed', 'failed', 'cancelled', 'unknown'] as const
  const mixed = states.map((state, i) => ({ ...rows[i], state }))
  render(<><section aria-label="范围一"><FilterableTaskProgressTable label="列表一" rows={mixed} /></section>
    <section aria-label="范围二"><FilterableTaskProgressTable label="列表二" rows={rows} /></section></>)
  const first = within(screen.getByRole('region', { name: '范围一' }))
  const second = within(screen.getByRole('region', { name: '范围二' }))
  for (const state of states) {
    await userEvent.selectOptions(first.getByLabelText('筛选任务状态'), state)
    expect(first.getByRole('status')).toHaveTextContent('显示 1 / 6 项')
    expect(first.getAllByRole('button', { name: '查看任务' })).toHaveLength(1)
    expect(second.getByLabelText('筛选任务状态')).toHaveValue('all')
    expect(second.getByRole('status')).toHaveTextContent('显示 44 / 44 项')
  }
})
