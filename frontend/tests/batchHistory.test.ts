import { beforeEach, expect, it } from 'vitest'
import { saveBatchHistory, readBatchHistory, pruneBatchHistory } from '../src/processing/batchHistory'
import type { BatchTaskProgress } from '../src/components/BatchProcessor'

beforeEach(() => localStorage.clear())
it('freezes each range independently and never rewrites completed tasks as stopped', () => {
  const tasks = [{ id: 'r1', state: 'completed', jobId: 'j1' }] as BatchTaskProgress[]
  saveBatchHistory('old', tasks)
  tasks[0].state = 'cancelled'
  saveBatchHistory('new', tasks)
  expect(readBatchHistory('old')?.[0]).toMatchObject({ state: 'completed', jobId: 'j1' })
  expect(readBatchHistory('new')?.[0].state).toBe('cancelled')
})
it('prunes only summaries of ranges removed from admissions', () => {
  saveBatchHistory('old', [])
  saveBatchHistory('keep', [])
  localStorage.setItem('ndr:tasks:v1:batch:b1', 'original')
  pruneBatchHistory(new Set(['keep']))
  expect(readBatchHistory('old')).toBeNull()
  expect(readBatchHistory('keep')).toEqual([])
  expect(localStorage.getItem('ndr:tasks:v1:batch:b1')).toBe('original')
})
