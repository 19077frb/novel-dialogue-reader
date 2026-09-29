import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Link } from 'react-router-dom'

import { fetchJob, queryKeys } from '../api/books'
import {
  fetchJobRecovery,
  pauseJob,
  reconcileJob,
  resumeJob,
  runJobNow,
} from '../api/jobs'
import type { JobDetailOut } from '../api/types'

const TERMINAL_STATES = new Set(['COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL'])

export function isTerminalJob(state: JobDetailOut['state']): boolean {
  return TERMINAL_STATES.has(state)
}

export interface JobPanelProps {
  jobId: string
  /** 任务快照更新时回调（例如显示本次用量；终态时刷新累计用量）。 */
  onUpdate?: (job: JobDetailOut) => void
}

/**
 * 任务面板。
 *
 * - 展示的都是后端真实结算值：`calls` 是真实调用次数，`cached_windows` 是缓存命中窗口数。
 * - 恢复动作完全来自 `GET /api/jobs/{id}/recovery`：前端不自己推断，也不隐藏「可能计费」提示。
 * - 未知结果不会自动重发：只有用户点「确认重发」才会回到队列。
 */
export function JobPanel({ jobId, onUpdate }: JobPanelProps) {
  const queryClient = useQueryClient()
  const job = useQuery({
    queryKey: queryKeys.job(jobId),
    queryFn: ({ signal }) => fetchJob(jobId, signal),
    refetchInterval: (query) => {
      const data = query.state.data as JobDetailOut | undefined
      if (!data || isTerminalJob(data.state)) return false
      return 2000
    },
  })
  const recovery = useQuery({
    queryKey: ['job-recovery', jobId],
    queryFn: ({ signal }) => fetchJobRecovery(jobId, signal),
    refetchInterval: (query) => {
      const data = query.state.data as JobDetailOut | undefined
      if (!data) return 2000
      return isTerminalJob(data.state as JobDetailOut['state']) ? false : 2000
    },
  })

  const state = job.data?.state
  useEffect(() => {
    if (job.data && onUpdate) onUpdate(job.data)
  }, [job.data, onUpdate])

  const action = useMutation({
    mutationFn: async (value: string) => {
      if (value === 'pause') await pauseJob(jobId)
      else if (value === 'resume') await resumeJob(jobId)
      else if (value === 'run') await runJobNow(jobId)
      else if (value === 'reconcile_keep') await reconcileJob(jobId, 'keep_unknown')
      else if (value === 'reconcile_retry') await reconcileJob(jobId, 'retry')
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.job(jobId) })
      void queryClient.invalidateQueries({ queryKey: ['job-recovery', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['annotations'] })
    },
  })

  if (job.isPending) return <p className="hint">正在查询任务状态…</p>
  if (job.isError) return <p className="status-error">任务状态查询失败。</p>

  const info = recovery.data
  const recoveryActions = info?.actions ?? []
  return (
    <div className="ndr-job-panel" data-testid="job-panel">
      <p role="status" aria-live="polite">
        任务 {job.data.kind}
        {job.data.purpose ? ` · ${job.data.purpose}` : ''} ·{' '}
        <strong data-testid="job-state">{state}</strong>
      </p>
      <dl>
        <dt>窗口</dt>
        <dd>
          <span data-testid="job-windows-done">
            {job.data.windows_total - job.data.remaining_windows}
          </span>
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

      {info && (
        <div className="ndr-recovery" data-testid="job-recovery">
          <p className="hint" data-testid="recovery-summary">
            {info.summary}
          </p>
          {typeof info.retry_in_seconds === 'number' && info.retry_in_seconds > 0 && (
            <p className="hint" data-testid="recovery-retry-after">
              建议等待 {info.retry_in_seconds} 秒后再继续（限流退避上限）。
            </p>
          )}
          <div className="ndr-form-actions">
            {recoveryActions.map((item) =>
              item.action === 'open_settings' ? (
                <Link key={item.action} to="/settings/models" data-testid="job-action-open_settings">
                  {item.label}
                </Link>
              ) : item.action === 'wait' || item.action === 'new_job' ? (
                <span key={item.action} className="hint" data-testid={`job-action-${item.action}`}>
                  {item.label}
                </span>
              ) : (
                <button
                  key={item.action}
                  type="button"
                  disabled={action.isPending || item.action === 'wait'}
                  onClick={() => action.mutate(item.action)}
                  data-testid={`job-action-${item.action}`}
                  title={item.detail}
                >
                  {item.label}
                  {item.paid ? '（可能计费）' : ''}
                </button>
              ),
            )}
          </div>
          {recoveryActions.length > 0 && (
            <ul className="hint">
              {recoveryActions.map((item) => (
                <li key={item.action}>{item.detail}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {job.data.last_error && <p className="status-error">{job.data.last_error}</p>}
    </div>
  )
}
