import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, it } from 'vitest'
import { jobTaskTone, TaskProgressSummary, TaskProgressTable } from '../src/components/TaskProgressTable'
import { useListPagination } from '../src/components/ListPagination'

it('shares batch columns, badges and error/actions without losing hidden controls', async () => {
  const rows = Array.from({ length: 21 }, (_, i) => ({ id: String(i), state: 'failed' as const,
    stateLabel: '失败', type: '对白归属', chapter: `章节 ${i}`, window: '窗口 1', error: '模型限流',
    actions: <input aria-label={`草稿 ${i}`} defaultValue="" /> }))
  render(<TaskProgressTable label="任务" rows={rows} />)
  expect(screen.getByRole('table')).toHaveClass('ndr-batch-task-table')
  expect(screen.getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['状态', '处理类型', '章节', '窗口', '原因 / 错误详情', '操作'])
  await userEvent.type(screen.getByLabelText('草稿 0'), '保留')
  const nav = screen.getByRole('navigation', { name: '任务分页' })
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(screen.getByText('章节 20')).toBeVisible()
  await userEvent.click(within(nav).getByRole('button', { name: '上一页' }))
  expect(screen.getByLabelText('草稿 0')).toHaveValue('保留')
})

it('respects preview-owned pagination when jumping to hidden task details', async () => {
  function Preview() {
    const pagination = useListPagination(21)
    return <><button onClick={() => pagination.setPage(1)}>定位任务</button>
      <TaskProgressTable label="批量任务" pagination={pagination} rows={Array.from({ length: 21 }, (_, i) => ({
        id: String(i), state: 'running', stateLabel: '处理中', type: '人物识别', chapter: `章节 ${i}`, window: '全文',
      }))} /></>
  }
  render(<Preview />)
  await userEvent.click(screen.getByRole('button', { name: '定位任务' }))
  expect(screen.getByText('章节 20')).toBeVisible()
  expect(screen.queryByRole('row', { name: /章节 0/ })).not.toBeInTheDocument()
})

it('preserves unknown/reconciliation/paused labels while mapping only visual tones', () => {
  expect(jobTaskTone('NEEDS_RECONCILIATION')).toBe('failed')
  expect(jobTaskTone('PAUSING')).toBe('running')
  expect(jobTaskTone('PAUSED')).toBe('cancelled')
  expect(jobTaskTone('unrecognized')).toBe('unknown')
  render(<TaskProgressSummary total={3} finished={1} running={1} />)
  expect(screen.getByRole('progressbar')).toHaveAttribute('value', '1')
  expect(screen.getByText('已结束 1/3 项；当前并发 1 项')).toBeVisible()
})
