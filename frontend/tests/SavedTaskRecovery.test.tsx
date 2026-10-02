import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { beforeEach, expect, it, vi } from 'vitest'

import { SavedTaskRecovery } from '../src/components/SavedTaskRecovery'

const mocks = vi.hoisted(() => ({ books: vi.fn(), batches: vi.fn(), singles: vi.fn() }))
vi.mock('../src/api/books', () => ({ fetchBooks: mocks.books, queryKeys: { books: () => ['books'] } }))
vi.mock('../src/components/BatchProcessor', () => ({ restoreSavedBatches: mocks.batches }))
vi.mock('../src/processing/singleWorkflow', () => ({ restoreSavedSingles: mocks.singles }))

beforeEach(() => { localStorage.clear(); vi.resetAllMocks() })
function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(<StrictMode><QueryClientProvider client={client}><SavedTaskRecovery /></QueryClientProvider></StrictMode>)
}
function seed() { localStorage.setItem('ndr:tasks:v1:batch:old-book', '{"unchanged":true}') }

it('does not read the shelf when there are no saved queues', () => {
  mount()
  expect(mocks.books).not.toHaveBeenCalled()
  expect(mocks.batches).not.toHaveBeenCalled()
})
it('uses an empty whitelist for a new library and preserves foreign records', async () => {
  seed(); mocks.books.mockResolvedValue({ items: [], next_cursor: null })
  mount()
  await waitFor(() => expect(mocks.batches).toHaveBeenCalledWith(new Set()))
  expect(mocks.singles).toHaveBeenCalledWith(new Set())
  expect(mocks.batches).toHaveBeenCalledTimes(1)
  expect(mocks.singles).toHaveBeenCalledTimes(1)
  expect(localStorage.getItem('ndr:tasks:v1:batch:old-book')).toBe('{"unchanged":true}')
})
it('reads all shelf pages before restoring and reuses the first-page cache', async () => {
  seed()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['books'], { items: [{ id: 'current' }], next_cursor: 'page 2/+' })
  let finish!: (page: unknown) => void
  mocks.books.mockReturnValue(new Promise(resolve => { finish = resolve }))
  mount(client)
  await waitFor(() => expect(mocks.books).toHaveBeenCalledWith(expect.any(AbortSignal), 'page 2/+'))
  expect(mocks.batches).not.toHaveBeenCalled()
  finish({ items: [{ id: 'later-page' }], next_cursor: null })
  await waitFor(() => expect(mocks.batches).toHaveBeenCalledWith(new Set(['current', 'later-page'])))
  // StrictMode may abort and restart a pagination read; the cached first page is never fetched.
  expect(mocks.books.mock.calls.every(([, cursor]) => cursor === 'page 2/+')).toBe(true)
})
it('does not restore on read failure and offers a successful retry', async () => {
  seed(); mocks.books.mockRejectedValueOnce(new Error('连接失败')).mockResolvedValue({ items: [{ id: 'current' }], next_cursor: null })
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('连接失败')
  expect(mocks.batches).not.toHaveBeenCalled()
  expect(mocks.singles).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
  await waitFor(() => expect(mocks.batches).toHaveBeenCalledWith(new Set(['current'])))
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument())
})
it('rejects repeated pagination cursors instead of looping or partially restoring', async () => {
  seed(); mocks.books.mockResolvedValue({ items: [], next_cursor: 'same' })
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('书架分页异常')
  expect(mocks.batches).not.toHaveBeenCalled()
})
