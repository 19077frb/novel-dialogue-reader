import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it } from 'vitest'
import { ReaderWorkspace } from '../src/components/ReaderWorkspace'
import { useReaderLayoutStore } from '../src/stores/readerLayoutStore'

beforeEach(() => useReaderLayoutStore.setState({ sidebarOpen: true, sidebarSize: 25, tabs: {} }))
it('hides navigation without losing drafts or replacing the reading scroll container', async () => {
  render(<ReaderWorkspace chapterTitle="第一章" navigation={<textarea aria-label="书签草稿" defaultValue="" />}>
    <section data-testid="reading-scroll">原文</section>
  </ReaderWorkspace>)
  const draft = screen.getByLabelText('书签草稿')
  const text = screen.getByTestId('reading-scroll')
  text.scrollTop = 120
  // jsdom has zero-sized resize handles; set input directly to avoid synthetic pointer capture.
  fireEvent.change(draft, { target: { value: '待保存' } })
  await userEvent.click(screen.getByRole('button', { name: '隐藏阅读导航' }))
  expect(draft).not.toBeVisible()
  expect(text).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '显示阅读导航' }))
  expect(screen.getByLabelText('书签草稿')).toBe(draft)
  expect(draft).toHaveValue('待保存')
  expect(screen.getByTestId('reading-scroll')).toBe(text)
  expect(text.scrollTop).toBe(120)
})
it('provides a keyboard separator and retains the panels across viewport changes', () => {
  render(<ReaderWorkspace navigation={<span>目录内容</span>}><section data-testid="text">正文</section></ReaderWorkspace>)
  const text = screen.getByTestId('text')
  const separator = screen.getByRole('separator', { name: '调整阅读导航宽度' })
  act(() => separator.focus())
  fireEvent.keyDown(separator, { key: 'ArrowRight' })
  fireEvent(window, new Event('resize'))
  expect(screen.getByTestId('text')).toBe(text)
  expect(screen.getByText('目录内容')).toBeVisible()
})
