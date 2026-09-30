import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import * as jobsApi from '../src/api/jobs'
import type { JobDetailOut, JobRecoveryOut } from '../src/api/types'
import { JobPanel } from '../src/components/JobPanel'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/books', () => ({
  queryKeys: { job: (id: string) => ['job', id] },
  fetchJob: vi.fn(),
}))

vi.mock('../src/api/jobs', () => ({
  fetchJobRecovery: vi.fn(),
  pauseJob: vi.fn(),
  resumeJob: vi.fn(),
  runJobNow: vi.fn(),
  reconcileJob: vi.fn(),
}))

const JOB = {
  id: 'j1',
  kind: 'INFERENCE',
  purpose: 'process',
  state: 'NEEDS_RECONCILIATION',
  book_id: 'b1',
  book_version_id: 'v1',
  progress: { stage: 'reconcile', retry_in_seconds: 4 },
  checkpoint: null,
  last_error: 'PROVIDER_TIMEOUT: 超时',
  windows: [],
  remaining_windows: 1,
  windows_total: 3,
  calls: 3,
  cached_windows: 1,
  unknown_usage_runs: 2,
  usage: {},
  created_at: '2026-09-28T00:00:00+00:00',
  updated_at: '2026-09-28T00:00:00+00:00',
} as unknown as JobDetailOut

const RECOVERY = {
  job_id: 'j1',
  state: 'NEEDS_RECONCILIATION',
  summary: '有 2 次调用结果未知：系统**不会**自动重发，请选择保留未知或确认重发。',
  actions: [
    {
      action: 'reconcile_keep',
      label: '保留未知结果',
      detail: '保留记录并停止重发。',
      paid: false,
      endpoint: 'POST /api/jobs/{id}/reconcile',
    },
    {
      action: 'reconcile_retry',
      label: '确认重发这些窗口',
      detail: '窗口会回到队列并重新调用模型。',
      paid: true,
      endpoint: 'POST /api/jobs/{id}/reconcile',
    },
  ],
  windows_total: 3,
  windows_done: 2,
  remaining_windows: 1,
  unknown_runs: 2,
  unknown_usage_runs: 2,
  retry_in_seconds: 4,
  requires_credential: false,
  last_error: 'PROVIDER_TIMEOUT: 超时',
  updated_at: '2026-09-28T00:00:00+00:00',
} as unknown as JobRecoveryOut

describe('JobPanel', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchJob).mockReset()
    vi.mocked(jobsApi.fetchJobRecovery).mockReset()
    vi.mocked(jobsApi.pauseJob).mockReset()
    vi.mocked(jobsApi.reconcileJob).mockReset()
    vi.mocked(booksApi.fetchJob).mockResolvedValue(JOB)
    vi.mocked(jobsApi.fetchJobRecovery).mockResolvedValue(RECOVERY)
  })

  it('显示真实计数、未知用量、退避建议与后端给出的恢复动作', async () => {
    renderWithProviders(<JobPanel jobId="j1" />)

    expect(await screen.findByTestId('job-state')).toHaveTextContent('结果未知，需确认')
    expect(screen.getByTestId('job-calls')).toHaveTextContent('3')
    expect(screen.getByTestId('job-cached-windows')).toHaveTextContent('1')
    expect(screen.getByTestId('job-unknown-usage')).toHaveTextContent('2')
    expect(screen.getByTestId('recovery-summary')).toHaveTextContent('不会')
    expect(screen.getByTestId('recovery-retry-after')).toHaveTextContent('4')

    // 付费动作必须显式标注
    expect(screen.getByTestId('job-action-reconcile_retry')).toHaveTextContent('可能计费')
    expect(screen.getByTestId('job-action-reconcile_keep')).not.toHaveTextContent('可能计费')
  })

  it('任务操作失败显示具体错误，不静默忽略按钮结果', async () => {
    vi.mocked(jobsApi.reconcileJob).mockRejectedValue(new Error('任务正在变化，请重新读取后再试'))
    renderWithProviders(<JobPanel jobId="j1" />)
    await userEvent.click(await screen.findByTestId('job-action-reconcile_keep'))
    expect(await screen.findByRole('alert')).toHaveTextContent('任务正在变化，请重新读取后再试')
    expect(screen.getByTestId('job-action-reconcile_keep')).toBeEnabled()
  })

  it('保留未知与确认重发分别调用 reconcile 的不同动作', async () => {
    vi.mocked(jobsApi.reconcileJob).mockResolvedValue({})
    renderWithProviders(<JobPanel jobId="j1" />)
    await screen.findByTestId('job-recovery')

    await userEvent.click(screen.getByTestId('job-action-reconcile_keep'))
    await waitFor(() => expect(jobsApi.reconcileJob).toHaveBeenCalledWith('j1', 'keep_unknown'))

    await userEvent.click(screen.getByTestId('job-action-reconcile_retry'))
    await waitFor(() => expect(jobsApi.reconcileJob).toHaveBeenCalledWith('j1', 'retry'))
  })

  it('暂停动作直接调用 pause 接口', async () => {
    vi.mocked(booksApi.fetchJob).mockResolvedValue({ ...JOB, state: 'RUNNING' } as JobDetailOut)
    vi.mocked(jobsApi.fetchJobRecovery).mockResolvedValue({
      ...RECOVERY,
      state: 'RUNNING',
      actions: [
        {
          action: 'pause',
          label: '暂停',
          detail: '当前窗口结束后暂停。',
          paid: false,
          endpoint: 'POST /api/jobs/{id}/pause',
        },
      ],
    } as unknown as JobRecoveryOut)
    vi.mocked(jobsApi.pauseJob).mockResolvedValue(JOB)

    renderWithProviders(<JobPanel jobId="j1" />)
    await userEvent.click(await screen.findByTestId('job-action-pause'))
    await waitFor(() => expect(jobsApi.pauseJob).toHaveBeenCalledWith('j1'))
  })

  it('缺凭据时给出「去补充模型凭据」入口而不是空按钮', async () => {
    vi.mocked(booksApi.fetchJob).mockResolvedValue({ ...JOB, state: 'FAILED' } as JobDetailOut)
    vi.mocked(jobsApi.fetchJobRecovery).mockResolvedValue({
      ...RECOVERY,
      state: 'FAILED',
      requires_credential: true,
      actions: [
        {
          action: 'open_settings',
          label: '去补充模型凭据',
          detail: '该配置需要密钥但当前没有。',
          paid: false,
          endpoint: '/settings/models',
        },
        {
          action: 'run',
          label: '立即执行',
          detail: '手动触发本地调度器。',
          paid: true,
          endpoint: 'POST /api/jobs/{id}/run',
        },
      ],
    } as unknown as JobRecoveryOut)

    renderWithProviders(<JobPanel jobId="j1" />)
    const link = await screen.findByTestId('job-action-open_settings')
    expect(link).toHaveAttribute('href', '/settings/models')
    expect(screen.getByTestId('job-action-run')).toHaveTextContent('可能计费')
  })
})
