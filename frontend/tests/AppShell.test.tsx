import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from '../src/components/AppShell'

describe('AppShell', () => {
  it('keeps navigation destinations, active route and skip target accessible', () => {
    render(<MemoryRouter initialEntries={['/settings/models']}>
      <AppShell status={<p>连接状态</p>} recovery={<p>恢复任务</p>}><p>页面正文</p></AppShell>
    </MemoryRouter>)
    const nav = screen.getByRole('navigation', { name: '主导航' })
    expect(nav.querySelectorAll('a')).toHaveLength(4)
    expect(screen.getByRole('link', { name: '模型配置' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByRole('link', { name: '跳到主要内容' })).toHaveAttribute('href', '#ndr-main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'ndr-main')
    expect(screen.getByText('恢复任务')).toBeVisible()
  })

  it('keeps the current page mounted when health or recovery status refreshes', () => {
    const view = (status: string) => <MemoryRouter>
      <AppShell status={<p>{status}</p>} recovery={null}>
        <input aria-label="未保存草稿" defaultValue="" />
      </AppShell>
    </MemoryRouter>
    const { rerender } = render(view('正在连接'))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '保留编辑' } })
    rerender(view('服务已连接'))
    expect(screen.getByRole('textbox')).toHaveValue('保留编辑')
  })
})
