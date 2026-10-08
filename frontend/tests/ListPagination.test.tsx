import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect, useState } from 'react'
import { expect, it, vi } from 'vitest'
import { PaginatedItems } from '../src/components/ListPagination'

it('omits controls for short lists and pages long native lists with accessible boundaries', async () => {
  const items = Array.from({ length: 21 }, (_, i) => <li key={i}>条目 {i + 1}</li>)
  const { rerender } = render(<PaginatedItems label="任务" listTag="ul">{items.slice(0, 2)}</PaginatedItems>)
  expect(screen.queryByRole('navigation')).not.toBeInTheDocument()
  rerender(<PaginatedItems label="任务" listTag="ul">{items}</PaginatedItems>)
  expect(screen.getAllByRole('listitem')).toHaveLength(20)
  expect(screen.getByText('条目 21')).not.toBeVisible()
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(screen.getAllByRole('listitem')).toHaveLength(1)
  expect(screen.getByText('条目 21')).toBeVisible()
  expect(screen.getByRole('button', { name: '下一页' })).toHaveAttribute('title', '已是最后一页。')
  expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '上一页' }))
  expect(screen.getByText('条目 1')).toBeVisible()
})

it('keeps keyed drafts and running effects mounted and preserves them after reordering', async () => {
  const disposed = vi.fn()
  function Draft({ id }: { id: number }) {
    const [value, setValue] = useState('')
    useEffect(() => () => disposed(), [])
    return <input aria-label={`草稿 ${id}`} value={value} onChange={e => setValue(e.target.value)} />
  }
  const rows = [1, 2, 3].map(id => <Draft key={id} id={id} />)
  const { rerender } = render(<PaginatedItems label="编辑" pageSize={1}>{rows}</PaginatedItems>)
  await userEvent.type(screen.getByLabelText('草稿 1'), '未保存')
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(screen.getByLabelText('草稿 1')).not.toBeVisible()
  expect(disposed).not.toHaveBeenCalled()
  rerender(<PaginatedItems label="编辑" pageSize={1}>{[rows[1], rows[0], rows[2]]}</PaginatedItems>)
  expect(screen.getByLabelText('草稿 1')).toBeVisible()
  expect(screen.getByLabelText('草稿 1')).toHaveValue('未保存')
  expect(disposed).not.toHaveBeenCalled()
})

it('clamps pages when records disappear and resets only when scope changes', async () => {
  const rows = Array.from({ length: 5 }, (_, i) => <p key={i}>内容 {i}</p>)
  const { rerender } = render(<PaginatedItems label="列表" pageSize={2} scope="甲">{rows}</PaginatedItems>)
  const next = () => userEvent.click(screen.getByRole('button', { name: '下一页' }))
  await next(); await next()
  rerender(<PaginatedItems label="列表" pageSize={2} scope="甲">{rows.slice(0, 3)}</PaginatedItems>)
  expect(screen.getByText('内容 2')).toBeVisible()
  rerender(<PaginatedItems label="列表" pageSize={2} scope="甲">{rows}</PaginatedItems>)
  expect(screen.getByText('内容 2')).toBeVisible()
  rerender(<PaginatedItems label="列表" pageSize={2} scope="乙">{rows}</PaginatedItems>)
  expect(screen.getByText('内容 0')).toBeVisible()
})

it('loads a remote batch only after the loaded pages, retains failed page and permits retry', async () => {
  const load = vi.fn().mockRejectedValueOnce(new Error('网络中断')).mockResolvedValueOnce(null)
  function Example() {
    const [count, setCount] = useState(2)
    return <PaginatedItems label="远端任务" pageSize={1} hasMore={count === 2} loadMore={async () => {
      await load(); setCount(3)
    }}>{Array.from({ length: count }, (_, i) => <p key={i}>任务 {i + 1}</p>)}</PaginatedItems>
  }
  render(<Example />)
  const nav = screen.getByRole('navigation', { name: '远端任务分页' })
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(load).not.toHaveBeenCalled()
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(screen.getByRole('alert')).toHaveTextContent('网络中断')
  expect(screen.getByText('任务 2')).toBeVisible()
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  expect(screen.getByText('任务 3')).toBeVisible()
  expect(load).toHaveBeenCalledTimes(2)
})

it('does not skip new records when a remote batch fills a partially loaded page', async () => {
  function Example() {
    const [count, setCount] = useState(3)
    return <PaginatedItems label="分批列表" pageSize={2} hasMore={count === 3} loadMore={async () => setCount(6)}>
      {Array.from({ length: count }, (_, i) => <p key={i}>新增 {i + 1}</p>)}
    </PaginatedItems>
  }
  render(<Example />)
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(screen.getByText('新增 4')).toBeVisible()
  expect(screen.getByText('新增 5')).not.toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  expect(screen.getByText('新增 5')).toBeVisible()
})
