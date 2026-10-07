import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import TaskQueuePage from '../src/pages/TaskQueuePage'
import { fetchTaskQueue } from '../src/api/jobs'
import { renderWithProviders } from './helpers'
import { writeJournal } from '../src/processing/journal'

vi.mock('../src/api/jobs', () => ({ fetchTaskQueue: vi.fn() }))
vi.mock('../src/components/JobPanel', () => ({
  JOB_KIND_LABELS: { INFERENCE: '对白归属', CHARACTER_ROSTER: '人物识别' },
  JOB_STATE_LABELS: { RUNNING: '处理中', QUEUED: '排队中' },
  JobPanel: ({ jobId }: { jobId: string }) => <p>详情 {jobId}</p>,
}))
beforeEach(() => { localStorage.clear(); vi.clearAllMocks() })

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
