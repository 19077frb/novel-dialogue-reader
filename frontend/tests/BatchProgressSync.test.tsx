import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { clearBatchProgress, useBatchAnnotationRevisions, useBatchCatalogRevision, useBatchChapterProgress } from '../src/components/BatchProcessor'

const bookId = 'progress-sync-book'
const key = `ndr:tasks:v1:batch:${bookId}`
function record(revision = 1, state = 'dialogue', catalogRevision = 0, annotationRevision = 0) {
  return JSON.stringify({ schema: 1, execution: { bookId, bookVersionId: 'v1' }, snapshot: {
    startedAt: 100, revision, catalogRevision, running: state !== 'processed', tasks: [],
    chapterStates: { c1: { state, pendingTasks: state === 'processed' ? 0 : 1 } },
    annotationRevisions: { c1: annotationRevision },
  } })
}
function Viewer() {
  const chapters = useBatchChapterProgress(bookId)
  const catalog = useBatchCatalogRevision(bookId)
  const annotations = useBatchAnnotationRevisions(bookId)
  return <pre data-testid="progress">{JSON.stringify({ chapters, catalog, annotations })}</pre>
}
function read() { return JSON.parse(screen.getByTestId('progress').textContent!) }
function send(raw: string | null) {
  if (raw === null) localStorage.removeItem(key)
  else localStorage.setItem(key, raw)
  act(() => window.dispatchEvent(new StorageEvent('storage', { key, newValue: raw, storageArea: localStorage })))
}
afterEach(() => { cleanup(); clearBatchProgress(bookId); localStorage.clear(); vi.restoreAllMocks() })

it('receives another tab progress and completion without writes or execution', () => {
  render(<Viewer />)
  send(record())
  expect(read().chapters.c1.state).toBe('dialogue')
  const writes = vi.spyOn(Storage.prototype, 'setItem')
  const raw = record(2, 'processed', 1, 1)
  act(() => window.dispatchEvent(new StorageEvent('storage', { key, newValue: raw })))
  expect(read()).toEqual({ chapters: { c1: { state: 'processed', pendingTasks: 0 } }, catalog: 1, annotations: { c1: 1 } })
  expect(writes).not.toHaveBeenCalled()
  send(record(1)) // Late older events cannot restore stale running status.
  expect(read().chapters.c1.state).toBe('processed')
  send('{broken')
  expect(read().catalog).toBe(1)
  send(null)
  expect(read()).toEqual({ chapters: {}, catalog: 0, annotations: {} })
})

it('catches up on subscription after unmounted time and ignores unrelated records', () => {
  localStorage.setItem(key, record(4, 'processed', 2, 2))
  const view = render(<Viewer />)
  expect(read().catalog).toBe(2)
  act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'ndr:tasks:v1:lease:processing:x', newValue: record(99) })))
  expect(read().catalog).toBe(2)
  view.unmount()
  send(record(5, 'processed', 3, 3))
  render(<Viewer />)
  expect(read().catalog).toBe(3)
})
