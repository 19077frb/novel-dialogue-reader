import { useQuery } from '@tanstack/react-query'

import { fetchJob, queryKeys } from '../api/books'
import type { JobDetailOut } from '../api/types'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

export function isTerminalJob(state: JobDetailOut['state']): boolean {
  return TERMINAL_STATES.has(state)
}

/** 任务面板：只在任务未进入终态时轮询（约 2 秒），终态立即停止。 */
export function JobPanel({ jobId }: { jobId: string }) {
  const job = useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: ({ signal }) => fetchJob(jobId, signal),
    refetchInterval: (query) => {
      const data = query.state.data as JobDetailOut | undefined
      if (!data || isTerminalJob(data.state)) return false
      return 2000
    },
  })

  if (job.isPending) return <p className="hint">正在查询任务状态…</p>
  if (job.isError) return <p className="status-error">任务状态查询失败。</p>

  return (
    <div className="ndr-job-panel" data-testid="job-panel">
      <p>
        任务 {job.data.kind} · <strong>{job.data.state}</strong>
      </p>
      {job.data.progress && (
        <ul className="hint">
          {Object.entries(job.data.progress).map(([key, value]) => (
            <li key={key}>
              {key}: {String(value)}
            </li>
          ))}
        </ul>
      )}
      {job.data.last_error && <p className="status-error">{job.data.last_error}</p>}
    </div>
  )
}