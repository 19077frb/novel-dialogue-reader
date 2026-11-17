import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { BookCard } from '../src/components/BookCard'
import type { BookOut } from '../src/api/types'

const book: BookOut = {
  id: 'b1', title: '雨后的书架', format: 'EPUB', source_sha256: 'source', import_status: 'COMPLETED',
  read_position_cp: 0, reading_mode: 'initial', version: 1, last_read_chapter_title: '第一章 晴天',
  active_version_id: 'v1', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  active_version: {
    id: 'v1', encoding: 'xml', parser_version: '1', normalization_version: '1', canonical_sha256: 'canonical',
    canonical_length_cp: 28, warnings: ['一张插图缺失。'], cover_resource_id: 'r1', created_at: '2026-01-01T00:00:00Z',
  },
}

describe('BookCard cover', () => {
  it('loads the registered cover with a version cache key and decorative image semantics', () => {
    render(<BookCard book={book} />, { wrapper: MemoryRouter })
    const cover = screen.getByTestId('book-cover-image')
    expect(cover).toHaveAttribute('src', '/api/books/b1/resources/r1?v=v1')
    expect(cover).toHaveAttribute('alt', '')
    expect(cover).toHaveAttribute('loading', 'lazy')
    expect(cover).toHaveAttribute('decoding', 'async')
    expect(cover).toHaveAttribute('draggable', 'false')
    expect(screen.getByTestId('book-cover')).toHaveAttribute('aria-hidden', 'true')
    expect(screen.getByRole('heading', { name: book.title })).toBeVisible()
    expect(screen.getByText('最后读到 · 第一章 晴天')).toBeVisible()
  })

  it.each(['TXT', 'EPUB'] as const)('shows the book icon for %s without a cover', format => {
    render(<BookCard book={{ ...book, format, active_version: { ...book.active_version!, cover_resource_id: null } }} />, { wrapper: MemoryRouter })
    expect(screen.queryByTestId('book-cover-image')).not.toBeInTheDocument()
    expect(screen.getByTestId('book-cover').querySelector('svg')).toBeInTheDocument()
    expect(screen.getByTestId('book-cover')).not.toHaveAttribute('title')
    expect(screen.getByRole('link', { name: '开始阅读' })).toHaveAttribute('href', '/books/b1/read')
  })

  it('keeps an importing book identifiable while its active version is absent', () => {
    render(<BookCard book={{ ...book, active_version: null, active_version_id: null, import_status: 'RUNNING' }} />, { wrapper: MemoryRouter })
    expect(screen.queryByTestId('book-cover-image')).not.toBeInTheDocument()
    expect(screen.getByTestId('book-cover').querySelector('svg')).toBeInTheDocument()
    expect(screen.getByText('导入中')).toBeVisible()
  })

  it('falls back on an image error and keeps book details, warnings and deletion usable', async () => {
    const onDelete = vi.fn()
    render(<BookCard book={book} onDelete={onDelete} />, { wrapper: MemoryRouter })
    fireEvent.error(screen.getByTestId('book-cover-image'))
    expect(screen.queryByTestId('book-cover-image')).not.toBeInTheDocument()
    expect(screen.getByTestId('book-cover')).toHaveAttribute('title', '封面加载失败')
    expect(screen.getByTestId('book-cover').querySelector('svg')).toBeInTheDocument()
    await userEvent.click(screen.getByText('书籍信息'))
    expect(screen.getByText('28 字符')).toBeVisible()
    await userEvent.click(screen.getByText('导入警告（1）'))
    expect(screen.getByText('一张插图缺失。')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(onDelete).toHaveBeenCalledWith(book)
  })

  it('tries a changed resource or version again while retaining failure for an unchanged image', () => {
    const view = render(<BookCard book={book} />, { wrapper: MemoryRouter })
    fireEvent.error(screen.getByTestId('book-cover-image'))
    view.rerender(<BookCard book={{ ...book, title: '新的书名' }} />)
    expect(screen.queryByTestId('book-cover-image')).not.toBeInTheDocument()
    const changedResource = { ...book, active_version: { ...book.active_version!, cover_resource_id: 'r2' } }
    view.rerender(<BookCard book={changedResource} />)
    expect(screen.getByTestId('book-cover-image')).toHaveAttribute('src', '/api/books/b1/resources/r2?v=v1')
    fireEvent.error(screen.getByTestId('book-cover-image'))
    view.rerender(<BookCard book={{ ...changedResource, active_version_id: 'v2', active_version: { ...changedResource.active_version, id: 'v2' } }} />)
    expect(screen.getByTestId('book-cover-image')).toHaveAttribute('src', '/api/books/b1/resources/r2?v=v2')
    expect(screen.getByTestId('book-cover')).not.toHaveAttribute('title')
  })

  it('keeps reading suspended during deletion and restores both actions afterwards', async () => {
    const onDelete = vi.fn()
    const view = render(<BookCard book={book} onDelete={onDelete} deleting />, { wrapper: MemoryRouter })
    expect(screen.queryByRole('link', { name: '开始阅读' })).not.toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('正在清理书籍记录')
    const deleting = screen.getByRole('button', { name: '正在删除…' })
    expect(deleting).toBeDisabled()
    await userEvent.click(deleting)
    expect(onDelete).not.toHaveBeenCalled()
    view.rerender(<BookCard book={book} onDelete={onDelete} />)
    expect(screen.getByRole('link', { name: '开始阅读' })).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(onDelete).toHaveBeenCalledOnce()
  })

  it('opens the book reader through the existing reading link', async () => {
    render(<MemoryRouter><Routes>
      <Route path="/" element={<BookCard book={book} />} />
      <Route path="/books/b1/read" element={<h2>阅读页</h2>} />
    </Routes></MemoryRouter>)
    await userEvent.click(screen.getByRole('link', { name: '开始阅读' }))
    expect(screen.getByRole('heading', { name: '阅读页' })).toBeVisible()
  })
})
