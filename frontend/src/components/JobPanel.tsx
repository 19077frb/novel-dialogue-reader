import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'

import { fetchJob, queryKeys } from '../api/books'
import type { JobDetailOut } from '../api/types'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

export function isTerminalJob(state: JobDetailOut['state']): boolean {
  return TERMINAL_STATES.has(state)
}

export interface JobPanelProps {
  jobId: string
  /** 任务进入终态时回调一次（例如刷新标注投影与用量）。 */
  onTerminal?: (job: JobDetailOut) => void
}

/**
 * 任务面板：只在任务未进入终态时轮询（约 2 秒），终态立即停止。
 * 展示的都是后端真实结算值：`calls` 是真实发出的调用次数，`cached_windows` 是缓存命中的窗口数。
 */
export function JobPanel({ jobId, onTerminal }: JobPanelProps) {
  const job = useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: ({ signal }) => fetchJob(jobId, signal),
    refetchInterval: (query) => {
      const data = query.state.data as JobDetailOut | undefined
      if (!data || isTerminalJob(data.state)) return false
      return 2000
    },
  })

  const state = job.data?.state
  useEffect(() => {
    if (job.data && onTerminal) onTerminal(job.data)
  }, [job.data, onTerminal])

  if (job.isPending) return <p className="hint">正在查询任务状态…</p>
  if (job.isError) return <p className="status-error">任务状态查询失败。</p>

  return (
    <div className="ndr-job-panel" data-testid="job-panel">
      <p>
        任务 {job.data.kind}
        {job.data.purpose ? ` · ${job.data.purpose}` : ''} ·{' '}
        <strong data-testid="job-state">{state}</strong>
      </p>
      <dl>
        <dt>窗口</dt>
        <dd>
          <span data-testid="job-windows-done">{job.data.windows_total - job.data.remaining_windows}</span>
          {' / '}
          <span data-testid="job-windows-total">{job.data.windows_total}</span>
        </dd>
        <dt>真实调用次数</dt>
        <dd data-testid="job-calls">{job.data.calls}</dd>
        <dt>缓存命中窗口</dt>
        <dd data-testid="job-cached-windows">{job.data.cached_windows}</dd>
        <dt>未知用量尝试</dt>
        <dd data-testid="job-unknown-usage">{job.data.unknown_usage_runs}</dd>
      </dl>
      {job.data.progress && (
        <ul className="hint">
          {Object.entries(job.data.progress).map(([key, value]) => (
            <li key={key}>
              {key}: {String(value)}
            </li>
          ))}
        </ul>
      )}
      {job.data.state === 'NEEDS_RECONCILIATION' && (
        <p className="status-error" data-testid="job-reconcile-hint">
          有远程调用结果未知；系统不会自动重发，等待人工确认。
        </p>
      )}
      {job.data.last_error && <p className="status-error">{job.data.last_error}</p>}
    </div>
  )
}