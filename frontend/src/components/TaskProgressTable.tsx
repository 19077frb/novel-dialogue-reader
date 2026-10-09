import type { ReactNode } from 'react'
import { useState } from 'react'
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

const FILTER_LABELS = { all: '全部', failed: '失败', queued: '排队中', running: '处理中',
  completed: '已完成', cancelled: '已停止', unknown: '状态未知' } as const
type TaskFilter = keyof typeof FILTER_LABELS

/** Only for complete local collections; remote cursor tables must not hide unloaded tasks. */
export function FilterableTaskProgressTable({ rows, label, scope = '' }: {
  rows: TaskProgressRow[]; label: string; scope?: string;
}) {
  const [selection, setSelection] = useState<{ scope: string; value: TaskFilter }>({ scope, value: 'all' })
  const filter = selection.scope === scope ? selection.value : 'all'
  const counts = rows.reduce((result, row) => { result[row.state]++; return result },
    { all: rows.length, failed: 0, queued: 0, running: 0, completed: 0, cancelled: 0, unknown: 0 })
  const visible = filter === 'all' ? rows : rows.filter(row => row.state === filter)
  return <>
    <label className="ndr-field"><span>筛选任务状态</span>
      <select value={filter} onChange={event => setSelection({ scope, value: event.target.value as TaskFilter })}>
        {(Object.keys(FILTER_LABELS) as TaskFilter[]).map(value => <option key={value} value={value}>
          {FILTER_LABELS[value]}（{counts[value]}）
        </option>)}
      </select>
    </label>
    <p className="hint" role="status">显示 {visible.length} / {rows.length} 项</p>
    {!visible.length && <p>{filter === 'all' ? '没有范围任务。' : `没有“${FILTER_LABELS[filter]}”状态的任务。`}</p>}
    <TaskProgressTable rows={visible} label={label} scope={`${scope}:${filter}`} />
  </>
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
