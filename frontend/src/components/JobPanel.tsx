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
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'
import { OperationTimer } from './OperationTimer'
import { ReadErrorNotice } from './ReadErrorNotice'

export const JOB_STATE_LABELS: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '处理中', PAUSING: '正在停止', PAUSED: '已暂停',
  PARTIAL: '部分完成', COMPLETED: '已完成', FAILED: '失败',
  BUDGET_EXHAUSTED: '额度已用完', NEEDS_RECONCILIATION: '结果未知，需确认',
}
const JOB_KIND_LABELS: Record<string, string> = {
  IMPORT: '导入', INFERENCE: '对白归属', CHARACTER_ROSTER: '人物识别',
  CHARACTER_MERGE: '自动合并人物',
  RECHECK: '局部复核', RECOMPUTE: '重新计算', EXPORT: '导出',
}

export function isTerminalJob(state: JobDetailOut['state']): boolean {
  return TERMINAL_JOB_STATES.has(state)
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
  if (job.isError) return <ReadErrorNotice label="任务状态查询失败" error={job.error} retrying={job.isFetching} onRetry={() => void job.refetch()} />

  const info = recovery.data
  const recoveryActions = info?.actions ?? []
  return (
    <div className="ndr-job-panel" data-testid="job-panel">
      <p role="status" aria-live="polite">
        {JOB_KIND_LABELS[job.data.kind] ?? job.data.kind}任务
        {job.data.purpose === 'preview' ? ' · 试运行' : ''} ·{' '}
        <strong data-testid="job-state" title={state}>{JOB_STATE_LABELS[state ?? ''] ?? state}</strong>
      </p>
      {job.data.progress?.stage === 'rechecking' && (
        <p className="hint">正在复核本窗口全部对白：第 {String(job.data.progress.review_round)} / {String(job.data.progress.review_rounds)} 轮</p>
      )}
      {!!job.data.progress?.review_stopped && typeof job.data.progress.review_stopped === 'object' && (
        <div className="hint">{Object.values(job.data.progress.review_stopped).map((reason, index) => (
          <p key={index}>{String(reason)}</p>
        ))}</div>
      )}
      <OperationTimer startedAt={Date.parse(job.data.created_at)}
        finishedAt={state && isTerminalJob(state) ? Date.parse(job.data.updated_at) : null}
        completed={job.data.windows_total - job.data.remaining_windows} total={job.data.windows_total} />
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
        <details><summary>任务诊断详情</summary><ul className="hint">
          {Object.entries(job.data.progress).map(([key, value]) => (
            <li key={key}>
              {key}: {String(value)}
            </li>
          ))}
        </ul></details>
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
                <Link className="ndr-button" key={item.action} to="/settings/models" data-testid="job-action-open_settings">
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
                  title={action.isPending ? '任务操作正在提交，请等待提交完成。' : item.detail}
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
      {action.isError && <p className="status-error" role="alert">{action.error instanceof Error ? action.error.message : '任务操作失败，请重试。'}</p>}
    </div>
  )
}
