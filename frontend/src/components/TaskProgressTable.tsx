import type { ReactNode } from 'react'
import { ListPagination, useListPagination } from './ListPagination'

export type TaskTone = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'unknown'
export function jobTaskTone(state: string): TaskTone {
  switch (state) {
    case 'QUEUED': return 'queued'
    case 'RUNNING': case 'PAUSING': return 'running'
    case 'COMPLETED': return 'completed'
    case 'FAILED': case 'PARTIAL': case 'BUDGET_EXHAUSTED': case 'NEEDS_RECONCILIATION': return 'failed'
    case 'PAUSED': return 'cancelled'
    default: return 'unknown'
  }
}

export interface TaskProgressRow {
  id: string; state: TaskTone; stateLabel: string; type: string
  book?: string; chapter: string; window: ReactNode; windowTitle?: string
  error?: string | null; actions?: ReactNode
}

/** Display-only table: preserve mounted controls and caller-owned pagination. */
export function TaskProgressTable({ rows, label, scope = '', showBook = false, pagination, rowTestId,
  hasMore = false, loading = false, loadMore }: {
  rows: TaskProgressRow[]; label: string; scope?: string; showBook?: boolean
  pagination?: ReturnType<typeof useListPagination>; rowTestId?: string
  hasMore?: boolean; loading?: boolean; loadMore?: () => Promise<unknown>
}) {
  const internal = useListPagination(rows.length, 20, scope)
  const pages = pagination ?? internal
  return <>
    <div className="ndr-table-wrap">
      <table className="ndr-batch-task-table" aria-label={label}>
        <thead><tr><th>状态</th><th>处理类型</th>{showBook && <th>书籍</th>}<th>章节</th><th>窗口</th><th>原因 / 错误详情</th><th>操作</th></tr></thead>
        <tbody>{rows.map((row, index) => <tr key={row.id} hidden={!pages.isVisible(index)} data-task-state={row.state} data-testid={rowTestId}>
          <td><span className={`ndr-task-state ndr-task-${row.state}`}>{row.stateLabel}</span></td>
          <td>{row.type}</td>{showBook && <td>{row.book || '书籍已删除'}</td>}
          <td>{row.chapter}</td><td title={row.windowTitle}>{row.window || '—'}</td>
          <td className={row.error ? 'status-error' : undefined}>{row.error || '—'}</td>
          <td>{row.actions || '—'}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <ListPagination key={scope} pagination={pages} label={label} hasMore={hasMore} loading={loading} loadMore={loadMore} />
  </>
}

export function TaskProgressSummary({ total, finished, running, label, testId }: {
  total: number; finished: number; running: number; label?: string; testId?: string
}) {
  return <div className="ndr-batch-progress-summary">
    <progress aria-label={label ?? '任务进度'} value={finished} max={Math.max(1, total)} />
    <span data-testid={testId}>已结束 {finished}/{total} 项；当前并发 {running} 项</span>
  </div>
}
