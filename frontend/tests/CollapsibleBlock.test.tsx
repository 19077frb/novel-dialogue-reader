import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useEffect, useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { CollapsibleBlock } from '../src/components/CollapsibleBlock'

describe('CollapsibleBlock', () => {
  it('keeps drafts and mounted work alive while hiding only the content', async () => {
    const cleanup = vi.fn()
    function Draft() {
      const [value, setValue] = useState('')
      useEffect(() => () => cleanup(), [])
      return <input aria-label="备注草稿" value={value} onChange={event => setValue(event.target.value)} />
    }
    const { rerender } = render(<CollapsibleBlock title="任务明细" summary="失败 1 项"><Draft /></CollapsibleBlock>)
    await userEvent.type(screen.getByLabelText('备注草稿'), '未保存的内容')
    const input = screen.getByLabelText('备注草稿')
    const toggle = screen.getByRole('button', { name: '收起任务明细' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById(toggle.getAttribute('aria-controls')!)).toContainElement(input)
    await userEvent.click(toggle)
    expect(input).not.toBeVisible()
    expect(screen.getByText('失败 1 项')).toBeVisible()
    expect(cleanup).not.toHaveBeenCalled()
    rerender(<CollapsibleBlock title="任务明细" summary="失败 2 项"><Draft /></CollapsibleBlock>)
    expect(input).not.toBeVisible()
    expect(screen.getByText('失败 2 项')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '展开任务明细' }))
    expect(input).toBeVisible()
    expect(input).toHaveValue('未保存的内容')
  })

  it('supports keyboard toggling and independently controlled blocks', async () => {
    const change = vi.fn()
    const { rerender } = render(<>
      <CollapsibleBlock title="第一块" defaultOpen={false}>正文一</CollapsibleBlock>
      <CollapsibleBlock title="第二块" open={false} onOpenChange={change}>正文二</CollapsibleBlock>
    </>)
    const first = screen.getByRole('button', { name: '展开第一块' })
    const second = screen.getByRole('button', { name: '展开第二块' })
    expect(first.getAttribute('aria-controls')).not.toEqual(second.getAttribute('aria-controls'))
    first.focus()
    await userEvent.keyboard('{Enter}')
    expect(screen.getByText('正文一')).toBeVisible()
    await userEvent.click(second)
    expect(change).toHaveBeenCalledWith(true)
    expect(screen.getByText('正文二')).not.toBeVisible()
    rerender(<>
      <CollapsibleBlock title="第一块" defaultOpen={false}>正文一</CollapsibleBlock>
      <CollapsibleBlock title="第二块" open onOpenChange={change}>正文二</CollapsibleBlock>
    </>)
    expect(screen.getByText('正文二')).toBeVisible()
  })
})
