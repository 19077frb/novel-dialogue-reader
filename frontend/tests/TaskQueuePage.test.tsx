import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import TaskQueuePage from '../src/pages/TaskQueuePage'
import { fetchTaskQueue, fetchRecentJobs } from '../src/api/jobs'
import { renderWithProviders } from './helpers'
import { writeJournal } from '../src/processing/journal'
import { saveBatchHistory } from '../src/processing/batchHistory'

vi.mock('../src/api/jobs', () => ({ fetchTaskQueue: vi.fn(), fetchRecentJobs: vi.fn() }))
vi.mock('../src/components/JobPanel', () => ({
  JOB_KIND_LABELS: { INFERENCE: '对白归属', CHARACTER_ROSTER: '人物识别' },
  JOB_STATE_LABELS: { RUNNING: '处理中', QUEUED: '排队中', COMPLETED: '已完成', NEEDS_RECONCILIATION: '待核对' },
  JobPanel: ({ jobId }: { jobId: string }) => <p>详情 {jobId}</p>,
}))
beforeEach(() => { localStorage.clear(); vi.clearAllMocks() })

function oldAdmission() {
  return { id: 'old-range', bookId: 'b1', versionId: 'v1', title: '旧批次', phase: 'cancelled',
    createdAt: 1, payload: { type: 'batch', work: { plans: [{ chapter: { id: 'c1', title: '第一章' }, estimate: { windows: [] } }] } } }
}

it('opens a task directly even when it is absent from the current queue page', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  renderWithProviders(<TaskQueuePage />, '/tasks?jobId=old-job')
  expect(screen.getByText('详情 old-job')).toBeVisible()
})

it('keeps historical completed tasks and details after a new batch replaces the book journal', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  writeJournal('admissions', [oldAdmission()])
  writeJournal('batch:b1', { execution: { queueId: 'new-range' }, snapshot: { tasks: [], running: false } })
  saveBatchHistory('old-range', [
    { id: 'r1', type: 'roster', chapterId: 'c1', chapterTitle: '第一章', windowId: null, windowLabel: '', state: 'completed', error: null, jobId: 'done-job' },
    { id: 'd1', type: 'dialogue', chapterId: 'c1', chapterTitle: '第一章', windowId: 'w1', windowLabel: '窗口 1', state: 'cancelled', error: null, jobId: 'stopped-job' },
  ])
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByLabelText('显示已结束任务'))
  await userEvent.click(within(screen.getByText('旧批次').closest('article')!).getByRole('button', { name: '展开范围内任务' }))
  expect(screen.getByText('第一章 · 人物识别 · 已完成')).toBeVisible()
  expect(screen.getByText('第一章 · 对白 窗口 1 · 已停止')).toBeVisible()
  await userEvent.click(screen.getAllByRole('button', { name: '查看任务' })[0])
  expect(screen.getByText('详情 done-job')).toBeVisible()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
})

it('does not invent stopped states for old missing records and reads actual chapter jobs on demand', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  vi.mocked(fetchRecentJobs).mockResolvedValue([{ id: 'orphan-job', state: 'NEEDS_RECONCILIATION', created_at: '2026-10-08T00:00:00Z' }] as never)
  writeJournal('admissions', [oldAdmission()])
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByLabelText('显示已结束任务'))
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  expect(screen.getByText('第一章 · 人物识别 · 状态待核实')).toBeVisible()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '展开本章后台任务' }))
  await userEvent.click(await screen.findByRole('button', { name: '查看任务' }))
  expect(screen.getByText('详情 orphan-job')).toBeVisible()
  expect(fetchRecentJobs).toHaveBeenCalledWith({ bookId: 'b1', versionId: 'v1', chapterId: 'c1', kind: 'CHARACTER_ROSTER', limit: 200 }, expect.anything())
})

it('shows cross-book progress and opens selected details without refreshing book data', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [
    { id: 'j1', book_title: '作品一', chapter_title: '第一章', kind: 'INFERENCE', state: 'RUNNING', windows_total: 3, windows_done: 1 },
    { id: 'j2', book_title: '作品二', chapter_title: '序章', kind: 'CHARACTER_ROSTER', state: 'QUEUED' },
  ], next_cursor: null } as never)
  renderWithProviders(<TaskQueuePage />)
  expect(await screen.findByText('作品一')).toBeVisible()
  expect(screen.getByText('作品二')).toBeVisible()
  expect(screen.getByText('窗口 1/3')).toBeVisible()
  await userEvent.click(screen.getAllByRole('button', { name: '查看任务' })[1])
  expect(screen.getByText('详情 j2')).toBeVisible()
  await userEvent.click(screen.getByLabelText('显示已结束任务'))
  await waitFor(() => expect(fetchTaskQueue).toHaveBeenCalledWith(false, null, expect.anything()))
})

it('shows a concrete queue read error with a retry button', async () => {
  vi.mocked(fetchTaskQueue).mockRejectedValue(new Error('数据库暂时繁忙'))
  renderWithProviders(<TaskQueuePage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('数据库暂时繁忙')
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  await userEvent.click(screen.getByRole('button', { name: '重新读取' }))
  expect(await screen.findByText('没有排队或运行的后台任务。')).toBeVisible()
})

it('includes automatic pending windows and reports malformed old records without dispatching', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  writeJournal('batch:b1', { execution: { bookId: 'b1', bookVersionId: 'v1', expandable: true, plans: [] },
    snapshot: { startedAt: 1, running: true, tasks: [{ id: 'dialogue:c1:w1', chapterTitle: '第三章', type: 'dialogue', windowLabel: '窗口 1', state: 'queued' }] } })
  localStorage.setItem('ndr:tasks:v1:single:old', 'invalid')
  renderWithProviders(<TaskQueuePage />)
  expect(screen.getByText('自动提前处理')).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  expect(screen.getByText('第三章 · 对白 窗口 1 · 等待执行')).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent('旧范围记录无法读取')
})
