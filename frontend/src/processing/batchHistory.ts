/** Display-only receipts; never used to dispatch or retry work. */
import type { BatchTaskProgress } from '../components/BatchProcessor'
import { readJournal, writeJournal, removeJournal } from './journal'

export function saveBatchHistory(queueId: string, tasks: BatchTaskProgress[]) {
  if (JSON.stringify(readBatchHistory(queueId)) !== JSON.stringify(tasks)) {
    writeJournal(`batch-history:${queueId}`, tasks)
  }
}

export function readBatchHistory(queueId: string) {
  return readJournal<BatchTaskProgress[]>(`batch-history:${queueId}`)
}

export function pruneBatchHistory(retainedIds: Set<string>) {
  for (const key of Object.keys(localStorage)) {
    const prefix = 'ndr:tasks:v1:batch-history:'
    if (key.startsWith(prefix) && !retainedIds.has(key.slice(prefix.length))) {
      removeJournal(key.slice('ndr:tasks:v1:'.length))
    }
  }
}
