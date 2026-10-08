import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import { BookmarkList } from '../src/components/BookmarkList'
import { fetchBookmarks, editBookmark } from '../src/api/bookmarks'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/bookmarks', () => ({ bookmarkKey: (id: string) => ['bookmarks', id],
  fetchBookmarks: vi.fn(), editBookmark: vi.fn(), deleteBookmark: vi.fn() }))
beforeEach(() => vi.clearAllMocks())

it('bounds the bookmark list, preserves unsaved notes and loads more only at the end', async () => {
  const items = Array.from({ length: 6 }, (_, i) => ({ id: `b${i}`, book_id: 'book', book_version_id: 'v1',
    chapter_id: 'chapter', chapter_title: `章节 ${i}`, excerpt: '原文', note: '', position_cp: i,
    version: 1, created_at: '2026-10-01T00:00:00Z' }))
  vi.mocked(fetchBookmarks).mockImplementation(async (_book, cursor) => ({
    items: cursor ? [{ ...items[0], id: 'new', chapter_title: '后续书签', position_cp: 7 }] : items,
    next_cursor: cursor ? null : 'next',
  }) as never)
  renderWithProviders(<BookmarkList bookId="book" activeVersionId="v1" />)
  const card = (await screen.findByText('章节 0')).closest('article')!
  await userEvent.click(within(card).getByText('编辑备注'))
  await userEvent.type(within(card).getByLabelText('书签备注'), '未保存的备注')
  const nav = screen.getByRole('navigation', { name: '书签分页' })
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(card).not.toBeVisible()
  expect(fetchBookmarks).toHaveBeenCalledTimes(1)
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(await screen.findByText('后续书签')).toBeVisible()
  await userEvent.click(within(nav).getByRole('button', { name: '上一页' }))
  expect(within(card).getByLabelText('书签备注')).toHaveValue('未保存的备注')
  expect(editBookmark).not.toHaveBeenCalled()
})
