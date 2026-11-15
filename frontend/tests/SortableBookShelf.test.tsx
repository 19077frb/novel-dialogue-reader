import { screen, within, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { SortableBookShelf } from '../src/components/SortableBookShelf'
import { LIBRARY_ORDER_KEY, useLibraryLayoutStore } from '../src/stores/libraryLayoutStore'
import type { BookOut } from '../src/api/types'
import { renderWithProviders } from './helpers'

const books = ['甲', '乙', '丙'].map((title, i) => ({ id: `b${i}`, title, format: 'TXT', import_status: 'COMPLETED', active_version: null, created_at: '2026-01-01' } as BookOut))
function titles() { return screen.getAllByTestId('book-card').map(card => within(card).getByRole('heading').textContent) }
beforeEach(() => { localStorage.removeItem(LIBRARY_ORDER_KEY); useLibraryLayoutStore.setState({ order: [] }) })
afterEach(() => vi.restoreAllMocks())

it('shows a quiet shelf, keeps edits as drafts and cancels without saving', async () => {
  renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  expect(screen.queryByRole('button', { name: /拖动/ })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '整理书架' }))
  expect(screen.getByRole('button', { name: '前移《甲》' })).toHaveAttribute('title', '已是当前列表第一本。')
  expect(screen.getByRole('button', { name: '后移《丙》' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: '后移《甲》' }))
  expect(titles()).toEqual(['乙', '甲', '丙'])
  expect(localStorage.getItem(LIBRARY_ORDER_KEY)).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(titles()).toEqual(['甲', '乙', '丙'])
  expect(useLibraryLayoutStore.getState().order).toEqual([])
})

it('supports keyboard-operated equivalent movement and saves across remounts', async () => {
  const view = renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '整理书架' }))
  screen.getByRole('button', { name: '后移《甲》' }).focus()
  await userEvent.keyboard('{Enter}')
  await userEvent.click(screen.getByRole('button', { name: '保存顺序' }))
  expect(JSON.parse(localStorage.getItem(LIBRARY_ORDER_KEY)!)).toEqual(['b1', 'b0', 'b2'])
  view.unmount()
  renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  expect(titles()).toEqual(['乙', '甲', '丙'])
})

it('retains usable session order and explains a failed persistence write', async () => {
  renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  await userEvent.click(screen.getByRole('button', { name: '整理书架' }))
  await userEvent.click(screen.getByRole('button', { name: '后移《甲》' }))
  await userEvent.click(screen.getByRole('button', { name: '保存顺序' }))
  expect(screen.getByText(/浏览器无法保存顺序/)).toBeVisible()
  expect(titles()).toEqual(['乙', '甲', '丙'])
})

it('announces keyboard drag and Escape cancellation without persisting', async () => {
  renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '整理书架' }))
  screen.getByRole('button', { name: '拖动《甲》调整顺序' }).focus()
  await userEvent.keyboard(' ')
  await waitFor(() => expect(screen.getByRole('button', { name: '拖动《甲》调整顺序' })).toHaveAttribute('aria-pressed', 'true'))
  await userEvent.keyboard('{Escape}')
  await waitFor(() => expect(screen.getByText('已取消本次拖动，位置未改变。')).toBeInTheDocument())
  expect(titles()).toEqual(['甲', '乙', '丙'])
  expect(localStorage.getItem(LIBRARY_ORDER_KEY)).toBeNull()
})

it('reorders with the drag handle keyboard sensor and saves only after confirmation', async () => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function(this: HTMLElement) {
    const item = this.closest('.ndr-sortable-book')
    const index = item ? [...document.querySelectorAll('.ndr-sortable-book')].indexOf(item) : 0
    return new DOMRect(index * 240, 0, 200, 320)
  })
  renderWithProviders(<SortableBookShelf books={books} onDelete={() => {}} />)
  await userEvent.click(screen.getByRole('button', { name: '整理书架' }))
  const handle = screen.getByRole('button', { name: '拖动《甲》调整顺序' })
  handle.focus()
  await userEvent.keyboard(' ')
  await waitFor(() => expect(handle).toHaveAttribute('aria-pressed', 'true'))
  await userEvent.keyboard('{ArrowRight}')
  await waitFor(() => expect(screen.getByText('《甲》将移到第 2 本的位置。')).toBeInTheDocument())
  await userEvent.keyboard(' ')
  await waitFor(() => expect(titles()).toEqual(['乙', '甲', '丙']))
  expect(localStorage.getItem(LIBRARY_ORDER_KEY)).toBeNull()
  await userEvent.click(screen.getByRole('button', { name: '保存顺序' }))
  expect(useLibraryLayoutStore.getState().order).toEqual(['b1', 'b0', 'b2'])
})
