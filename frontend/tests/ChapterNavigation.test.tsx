import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as batch from '../src/components/BatchProcessor'

import { ChapterNavigation } from '../src/components/ChapterNavigation'
import type { ChapterOut } from '../src/api/types'
import { updateGeneralSettings } from '../src/settings/preferences'

const CHAPTERS = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 10, source_href: null },
  { id: 'c2', ordinal: 1, title: '第二章', start_cp: 10, end_cp: 20, source_href: null },
  { id: 'c3', ordinal: 2, title: '第三章', start_cp: 20, end_cp: 30, source_href: null },
] as ChapterOut[]

describe('ChapterNavigation', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear() })
  it('双击切换默认关闭，不写入章节状态', () => {
    const save = vi.fn()
    render(<ChapterNavigation chapters={CHAPTERS} activeChapterId="c1" onSelect={vi.fn()} onSetProcessingStatus={save} />)
    fireEvent.doubleClick(screen.getByText('第一章'))
    expect(save).not.toHaveBeenCalled()
  })
  it.each(['unprocessed', 'queued', 'roster', 'dialogue', 'failed', 'stopped'] as const)(
    '%s 先双击标为完成，再次双击改为未处理，实时进度不覆盖手动状态', async state => {
      updateGeneralSettings({ doubleClickChapterStatus: true })
      const save = vi.fn(async (chapter: ChapterOut, processed: boolean) => ({ ...chapter,
        dialogue_processed: processed, processing_status_override: processed }))
      const select = vi.fn()
      render(<ChapterNavigation bookId="b1" chapters={CHAPTERS} activeChapterId="c1" onSelect={select} onSetProcessingStatus={save}
        processingStates={{ c1: { state, completedWindows: 0, totalWindows: 1, error: null } }} />)
      fireEvent.click(screen.getByText('第一章'), { detail: 1 })
      fireEvent.doubleClick(screen.getByText('第一章'))
      await waitFor(() => expect(screen.getByText('第一章').closest('button')).toHaveAttribute('data-processing-state', 'processed'))
      expect(screen.queryByRole('button', { name: '取消第一章的任务' })).not.toBeInTheDocument()
      expect(save).toHaveBeenLastCalledWith(CHAPTERS[0], true)
      fireEvent.doubleClick(screen.getByText('第一章'))
      await waitFor(() => expect(screen.getByText('第一章').closest('button')).toHaveAttribute('data-processing-state', 'unprocessed'))
      expect(screen.queryByRole('button', { name: '取消第一章的任务' })).not.toBeInTheDocument()
      expect(save).toHaveBeenLastCalledWith(CHAPTERS[0], false)
      expect(select).not.toHaveBeenCalled()
    })
  it('已完成章节双击变为未处理，保存失败保留原状态并显示具体错误', async () => {
    updateGeneralSettings({ doubleClickChapterStatus: true })
    const save = vi.fn().mockRejectedValue(new Error('数据库繁忙'))
    render(<ChapterNavigation chapters={[{ ...CHAPTERS[0], dialogue_processed: true }]} activeChapterId="c1" onSelect={vi.fn()} onSetProcessingStatus={save} />)
    fireEvent.doubleClick(screen.getByText('第一章'))
    expect(await screen.findByRole('alert')).toHaveTextContent('数据库繁忙')
    expect(save).toHaveBeenCalledWith(expect.anything(), false)
    expect(screen.getByText('第一章').closest('button')).toHaveAttribute('data-processing-state', 'processed')
  })
  it('手动未处理重新入队后显示新队列和取消图标，不继续显示旧的未处理保护', () => {
    render(<ChapterNavigation bookId="b1" chapters={[{ ...CHAPTERS[0], dialogue_processed: false, processing_status_override: false }]}
      activeChapterId="c1" onSelect={vi.fn()} processingStates={{ c1: {
        state: 'queued', completedWindows: 0, totalWindows: 2, pendingTasks: 3, error: null, manualStatusCleared: true,
      } }} />)
    expect(screen.getByText('第一章').closest('button')).toHaveAttribute('data-processing-state', 'queued')
    expect(screen.getByRole('button', { name: '取消第一章的任务' })).toBeEnabled()
  })
  it('章节旁显示重试图标，点击只重试本章而不跳转阅读', async () => {
    const retry = vi.spyOn(batch, 'retryChapterProcessing').mockResolvedValue()
    const select = vi.fn()
    render(<ChapterNavigation bookId="b1" chapters={CHAPTERS} activeChapterId="c2" onSelect={select}
      processingStates={{ c1: { state: 'failed', completedWindows: 1, totalWindows: 2, error: '失败', pendingTasks: 0 } }} />)
    const icon = screen.getByRole('button', { name: '重试第一章的任务' })
    expect(icon.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    expect(icon.closest('li')).toHaveTextContent('第一章')
    fireEvent.click(icon)
    await waitFor(() => expect(retry).toHaveBeenCalledWith('b1', 'c1'))
    expect(select).not.toHaveBeenCalled()
  })

  it('排队和执行的章节显示取消图标，即使某窗口已失败也优先取消剩余任务', async () => {
    const cancel = vi.spyOn(batch, 'cancelChapterProcessing').mockResolvedValue()
    render(<ChapterNavigation bookId="b1" chapters={CHAPTERS.map(chapter => ({ ...chapter, dialogue_processed: true }))} activeChapterId="c2" onSelect={vi.fn()}
      processingStates={{ c1: { state: 'queued', completedWindows: 0, totalWindows: 2, error: null },
        c2: { state: 'failed', completedWindows: 0, totalWindows: 2, error: '窗口失败', pendingTasks: 1 } }} />)
    fireEvent.click(screen.getByRole('button', { name: '取消第二章的任务' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('b1', 'c2'))
    expect(screen.getByRole('button', { name: '取消第一章的任务' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: '重试第二章的任务' })).not.toBeInTheDocument()
  })

  it('取消收尾时禁用图标，操作失败时显示具体错误', async () => {
    vi.spyOn(batch, 'retryChapterProcessing').mockRejectedValue(new Error('后台请求结果不明确'))
    render(<ChapterNavigation bookId="b1" chapters={CHAPTERS} activeChapterId="c2" onSelect={vi.fn()}
      processingStates={{ c1: { state: 'failed', completedWindows: 0, totalWindows: 2, error: '失败' },
        c2: { state: 'dialogue', completedWindows: 0, totalWindows: 2, error: null, cancelRequested: true } }} />)
    expect(screen.getByRole('button', { name: '取消第二章的任务' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /第二章.*正在取消/ })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '重试第一章的任务' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('后台请求结果不明确')
  })
  it('保存的完成状态覆盖旧失败，但正在重做时仍显示实时进度', () => {
    render(<ChapterNavigation chapters={CHAPTERS.map(chapter => ({ ...chapter, dialogue_processed: true }))}
      activeChapterId="c1" onSelect={vi.fn()} processingStates={{
        c1: { state: 'failed', completedWindows: 1, totalWindows: 2, error: '旧失败' },
        c2: { state: 'dialogue', completedWindows: 0, totalWindows: 2, error: null },
      }} />)
    expect(screen.getByRole('button', { name: /第一章/ })).toHaveAttribute('data-processing-state', 'processed')
    expect(screen.getByRole('button', { name: /第一章/ })).not.toHaveAttribute('title')
    expect(screen.getByRole('button', { name: /第二章/ })).toHaveAttribute('data-processing-state', 'dialogue')
  })
  it('用不同状态和颜色类实时标记章节处理进度', () => {
    render(
      <ChapterNavigation
        chapters={CHAPTERS}
        activeChapterId="c2"
        onSelect={vi.fn()}
        processingStates={{
          c1: { state: 'processed', completedWindows: 2, totalWindows: 2, error: null },
          c2: { state: 'dialogue', completedWindows: 1, totalWindows: 3, error: null },
          c3: { state: 'queued', completedWindows: 0, totalWindows: 2, error: null },
        }}
      />,
    )

    expect(screen.getByTestId('chapter-status-legend')).toHaveTextContent('未处理排队中识别人物处理对白已完成失败/已停止')
    expect(screen.getByRole('button', { name: /第一章/ })).toHaveAttribute('data-processing-state', 'processed')
    expect(screen.getByRole('button', { name: /第二章.*1\/3/ })).toHaveAttribute('data-processing-state', 'dialogue')
    expect(screen.getByRole('button', { name: /第三章/ })).toHaveAttribute('data-processing-state', 'queued')
  })

  it('没有实时批次状态时使用后端保存的章节状态', () => {
    const chapters = CHAPTERS.map((chapter) => ({
      ...chapter,
      dialogue_processed: chapter.id === 'c3',
    }))
    render(
      <ChapterNavigation
        chapters={chapters}
        activeChapterId={null}
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /第一章/ })).toHaveAttribute('data-processing-state', 'unprocessed')
    expect(screen.getByRole('button', { name: /第三章/ })).toHaveAttribute('data-processing-state', 'processed')
  })
})
