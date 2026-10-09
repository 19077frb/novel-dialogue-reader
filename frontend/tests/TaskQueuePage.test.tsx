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

it('uses the same detail button for queued and running tasks and follows the queued receipt without querying history', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  const item = { ...oldAdmission(), id: 'range', phase: 'running' }
  const tasks = [
    { id: 'roster:c1', type: 'roster', chapterId: 'c1', chapterTitle: '第一章', windowLabel: '全文', state: 'running', jobId: 'roster-job' },
    { id: 'dialogue:c1:w1', type: 'dialogue', chapterId: 'c1', chapterTitle: '第一章', windowId: 'w1', windowLabel: '窗口 1', state: 'queued' },
  ]
  writeJournal('admissions', [item])
  writeJournal('batch:b1', { execution: { queueId: 'range' }, snapshot: { startedAt: 1, running: true, tasks } })
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  const table = screen.getByRole('table', { name: '范围内任务' })
  expect(within(table).getAllByRole('button', { name: '查看任务' })).toHaveLength(2)
  expect(within(table).queryByText('本章后台任务')).not.toBeInTheDocument()
  await userEvent.click(within(table).getAllByRole('button', { name: '查看任务' })[1])
  expect(screen.getByRole('table', { name: '当前任务' })).toHaveTextContent('排队中对白归属第一章窗口 1')
  expect(screen.getByText(/本任务尚未派发/)).toBeVisible()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
  writeJournal('batch:b1', { execution: { queueId: 'range' }, snapshot: { startedAt: 1, running: true,
    tasks: [tasks[0], { ...tasks[1], state: 'running', jobId: 'dialogue-job' }] } })
  await waitFor(() => expect(screen.getByText('详情 dialogue-job')).toBeVisible(), { timeout: 3500 })
  expect(fetchRecentJobs).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '关闭详情' }))
  expect(screen.queryByText('详情 dialogue-job')).not.toBeInTheDocument()
})

it('does not switch an old planned selection to a replacement batch with the same window id', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  const item = { ...oldAdmission(), id: 'old-range', phase: 'running', payload: { type: 'batch', work: { plans: [] } } }
  const task = { id: 'dialogue:c1:w1', type: 'dialogue', chapterId: 'c1', chapterTitle: '第一章', windowId: 'w1', windowLabel: '窗口 1', state: 'queued' }
  writeJournal('admissions', [item])
  writeJournal('batch:b1', { execution: { queueId: 'old-range' }, snapshot: { startedAt: 1, running: true, tasks: [task] } })
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  await userEvent.click(screen.getByRole('button', { name: '查看任务' }))
  writeJournal('batch:b1', { execution: { queueId: 'new-range' }, snapshot: { startedAt: 2, running: true, tasks: [{ ...task, jobId: 'unrelated-job' }] } })
  await screen.findByText(/原任务记录已更新或缺失/, {}, { timeout: 3500 })
  expect(screen.queryByText('详情 unrelated-job')).not.toBeInTheDocument()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
})

it.each(['single', 'merge'])('provides the unified detail entry for an undispatched %s range', async type => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  writeJournal('admissions', [{ ...oldAdmission(), phase: 'queued', payload: type === 'single'
    ? { type: 'single', work: { tasks: [{ windowId: 'w1', ordinal: '1', job: null, error: null }] } }
    : { type: 'merge', work: {} } }])
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  await userEvent.click(screen.getByRole('button', { name: '查看任务' }))
  expect(screen.getByText(/本任务尚未派发/)).toBeVisible()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
})

it('shows a waiting batch plan before its execution journal exists without querying chapter history', async () => {
  vi.mocked(fetchTaskQueue).mockResolvedValue({ items: [], next_cursor: null })
  writeJournal('admissions', [{ ...oldAdmission(), phase: 'queued' }])
  renderWithProviders(<TaskQueuePage />)
  await userEvent.click(screen.getByRole('button', { name: '展开范围内任务' }))
  await userEvent.click(screen.getByRole('button', { name: '查看任务' }))
  expect(screen.getByRole('table', { name: '当前任务' })).toHaveTextContent('排队中人物识别第一章全文')
  expect(screen.getByText(/本任务尚未派发/)).toBeVisible()
  expect(screen.queryByText('本章后台任务')).not.toBeInTheDocument()
  expect(fetchRecentJobs).not.toHaveBeenCalled()
  writeJournal('batch:b1', { execution: { queueId: 'old-range' }, snapshot: { startedAt: 1, running: true,
    tasks: [{ id: 'roster:c1', type: 'roster', chapterId: 'c1', chapterTitle: '第一章', windowLabel: '全文', state: 'running', jobId: 'started-roster' }] } })
  await screen.findByText('详情 started-roster', {}, { timeout: 3500 })
  expect(fetchRecentJobs).not.toHaveBeenCalled()
})

it('pages background tasks and loads the next cursor only after all loaded pages', async () => {
  const rows = Array.from({ length: 21 }, (_, i) => ({ id: `j${i}`, book_title: `作品 ${i}`, chapter_title: '第一章', kind: 'INFERENCE', state: 'COMPLETED' }))
  vi.mocked(fetchTaskQueue).mockImplementation(async (_active, cursor) => ({
    items: cursor ? [{ ...rows[0], id: 'remote', book_title: '下一批作品' }] : rows,
    next_cursor: cursor ? null : 'cursor-2',
  }) as never)
  renderWithProviders(<TaskQueuePage />)
  await screen.findByText('作品 0')
  const nav = screen.getByRole('navigation', { name: '后台任务分页' })
  expect(screen.getAllByRole('button', { name: '查看任务' })).toHaveLength(20)
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  expect(screen.getByText('作品 20')).toBeVisible()
  expect(fetchTaskQueue).toHaveBeenCalledTimes(1)
  await userEvent.click(screen.getAllByRole('button', { name: '查看任务' })[0])
  expect(screen.getByText('详情 j20')).toBeVisible()
  await userEvent.click(within(nav).getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(screen.getByText('下一批作品')).toBeVisible())
  expect(fetchTaskQueue).toHaveBeenCalledWith(true, 'cursor-2', expect.anything())
  expect(screen.getByText('详情 j20')).toBeVisible()
})

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
  expect(screen.getByRole('row', { name: /已完成 人物识别 第一章/ })).toBeVisible()
  expect(screen.getByRole('row', { name: /已停止 对白归属 第一章 窗口 1/ })).toBeVisible()
  expect(screen.getByRole('progressbar', { name: '旧批次任务进度' })).toHaveAttribute('value', '2')
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
  expect(screen.getByRole('row', { name: /状态待核实 人物识别 第一章/ })).toBeVisible()
  expect(screen.getByRole('progressbar', { name: '旧批次任务进度' })).toHaveAttribute('value', '0')
  expect(fetchRecentJobs).not.toHaveBeenCalled()
  await userEvent.click(screen.getByRole('button', { name: '查看任务' }))
  await userEvent.click(screen.getByRole('button', { name: '展开本章后台任务' }))
  await screen.findByText(/待核对/)
  await userEvent.click(within(screen.getByText('任务详情').closest('section')!).getByRole('button', { name: '查看任务' }))
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
  const table = screen.getByRole('table', { name: '后台任务' })
  expect(table).toHaveClass('ndr-batch-task-table')
  expect(within(table).getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['状态', '处理类型', '书籍', '章节', '窗口', '原因 / 错误详情', '操作'])
  expect(screen.getByRole('row', { name: /处理中 对白归属/ })).toHaveAttribute('data-task-state', 'running')
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
  expect(screen.getByRole('row', { name: /排队中 对白归属 第三章 窗口 1/ })).toBeVisible()
  expect(screen.getByRole('alert')).toHaveTextContent('旧范围记录无法读取')
})
