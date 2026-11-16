import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as batch from '../src/components/BatchProcessor'

import { ChapterNavigation } from '../src/components/ChapterNavigation'
import type { ChapterOut, TocNodeOut } from '../src/api/types'
import { updateGeneralSettings } from '../src/settings/preferences'

const CHAPTERS = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 10, source_href: null },
  { id: 'c2', ordinal: 1, title: '第二章', start_cp: 10, end_cp: 20, source_href: null },
  { id: 'c3', ordinal: 2, title: '第三章', start_cp: 20, end_cp: 30, source_href: null },
] as ChapterOut[]

describe('ChapterNavigation', () => {
  beforeEach(() => { vi.restoreAllMocks(); localStorage.clear() })
  const node = (id: string, parent_id: string | null, title: string, chapter_id: string | null = null, target_cp: number | null = null): TocNodeOut =>
    ({ id, parent_id, title, chapter_id, target_cp })
  it('保留包装根和多级同名目录，父章可跳转，摘要去重别名，遗漏章节仍可读', () => {
    const select = vi.fn()
    const toc = [node('root', null, '全书'), node('v1', 'root', '卷'),
      node('parent', 'v1', '上篇', 'c1', 2), node('child', 'parent', '子目', 'c2', 12),
      node('alias', 'parent', '别名', 'c2', 15), node('v2', 'root', '卷')]
    render(<ChapterNavigation bookId="book" chapters={CHAPTERS} toc={toc} activeChapterId="c2" onSelect={select}
      processingStates={{ c2: { state: 'failed', completedWindows: 0, totalWindows: 1, error: '失败' } }} />)
    expect(screen.getByRole('button', { name: '收起全书' })).toBeVisible()
    expect(screen.getByRole('button', { name: '收起卷' })).toBeVisible()
    expect(screen.getByRole('button', { name: '收起上篇' })).toBeVisible()
    const summary = screen.getByRole('group', { name: /^全书：共 2 章/ })
    expect(within(summary).getByLabelText('失败/已停止 1 章')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /上篇.*未处理/ }))
    expect(select).toHaveBeenLastCalledWith(CHAPTERS[0], 2)
    fireEvent.click(screen.getByRole('button', { name: /别名.*失败/ }))
    expect(select).toHaveBeenLastCalledWith(CHAPTERS[1], 15)
    expect(screen.getByRole('button', { name: /第三章/ })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '收起上篇' }))
    expect(screen.queryByRole('button', { name: /子目.*失败/ })).not.toBeInTheDocument()
    expect(within(summary).getByLabelText('失败/已停止 1 章')).toBeVisible()
    expect(screen.getByRole('button', { name: /上篇.*未处理/ })).toBeVisible()
  })
  it('显式空目录平铺，旧数据继续按卷分组；标题分隔符不修改新目录标签', () => {
    const chapters = [{ ...CHAPTERS[0], title: '第一卷 · 原章节' }]
    const { rerender } = render(<ChapterNavigation chapters={chapters} toc={[]} activeChapterId={null} onSelect={vi.fn()} />)
    expect(screen.queryByRole('button', { name: '展开第一卷' })).not.toBeInTheDocument()
    expect(screen.getByText('第一卷 · 原章节')).toBeVisible()
    rerender(<ChapterNavigation chapters={chapters} toc={[node('one', null, '标题 · 保留', 'c1')]} activeChapterId={null} onSelect={vi.fn()} />)
    expect(screen.getByText('标题 · 保留')).toBeVisible()
    rerender(<ChapterNavigation chapters={chapters} toc={null} activeChapterId={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: '展开第一卷' })).toBeVisible()
  })
  it('同名目录不共享展开状态，切换当前章节展开全部祖先，切书清除展开状态', () => {
    const toc = [node('root', null, '全书'), node('v1', 'root', '相同'), node('c1', 'v1', '第一章', 'c1'),
      node('v2', 'root', '相同'), node('c2', 'v2', '第二章', 'c2')]
    const { rerender } = render(<ChapterNavigation bookId="a" chapters={CHAPTERS} toc={toc} activeChapterId="c1" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: '收起相同' })).toBeVisible()
    expect(screen.getByRole('button', { name: '展开相同' })).toBeVisible()
    rerender(<ChapterNavigation bookId="a" chapters={CHAPTERS} toc={toc} activeChapterId="c2" onSelect={vi.fn()} />)
    expect(screen.getAllByRole('button', { name: '收起相同' })).toHaveLength(2)
    rerender(<ChapterNavigation bookId="b" chapters={CHAPTERS} toc={toc} activeChapterId="c2" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: '展开相同' })).toBeVisible()
    expect(screen.getByRole('button', { name: '收起相同' })).toBeVisible()
    expect(screen.getByRole('button', { name: /第二章/ })).toHaveAttribute('aria-current', 'true')
  })
  it('无效目标及断开的父目录不隐藏章节，循环引用也不导致递归失败', () => {
    render(<ChapterNavigation chapters={CHAPTERS} activeChapterId="c1" onSelect={vi.fn()}
      toc={[node('a', 'b', '环甲', 'c1'), node('b', 'a', '环乙'), node('orphan', 'missing', '孤立标题', 'missing')]} />)
    expect(screen.getByRole('button', { name: /环甲/ })).toBeVisible()
    expect(screen.getByText('孤立标题')).toBeVisible()
    expect(screen.getByRole('button', { name: /第二章/ })).toBeVisible()
    expect(screen.getByRole('button', { name: /第三章/ })).toBeVisible()
  })
  it('新目录的别名共享实时任务和双击保存状态，保留重试与取消', async () => {
    updateGeneralSettings({ doubleClickChapterStatus: true })
    const retry = vi.spyOn(batch, 'retryChapterProcessing').mockResolvedValue()
    const cancel = vi.spyOn(batch, 'cancelChapterProcessing').mockResolvedValue()
    const save = vi.fn(async (chapter: ChapterOut, processed: boolean) => ({ ...chapter,
      dialogue_processed: processed, processing_status_override: processed }))
    render(<ChapterNavigation bookId="book" chapters={CHAPTERS} activeChapterId="c1" onSelect={vi.fn()}
      onSetProcessingStatus={save}
      toc={[node('root', null, '全书'), node('a', 'root', '别名甲', 'c1'), node('b', 'root', '别名乙', 'c1'), node('c', 'root', '排队章', 'c2')]}
      processingStates={{ c1: { state: 'failed', completedWindows: 0, totalWindows: 1, error: '失败' },
        c2: { state: 'queued', completedWindows: 0, totalWindows: 1, pendingTasks: 1, error: null } }} />)
    fireEvent.click(screen.getAllByRole('button', { name: '重试第一章的任务' })[0])
    await waitFor(() => expect(retry).toHaveBeenCalledWith('book', 'c1'))
    fireEvent.click(screen.getByRole('button', { name: '取消第二章的任务' }))
    await waitFor(() => expect(cancel).toHaveBeenCalledWith('book', 'c2'))
    fireEvent.doubleClick(screen.getByText('别名甲'))
    await waitFor(() => expect(screen.getByText('别名乙').closest('button')).toHaveAttribute('data-processing-state', 'processed'))
    expect(save).toHaveBeenCalledWith(CHAPTERS[0], true)
    const summary = screen.getByRole('group', { name: /^全书：共 2 章/ })
    expect(within(summary).getByLabelText('已完成 1 章')).toBeVisible()
  })
  it('重复节点ID不会重复渲染或漏章，动态章节状态更新摘要且保留折叠', () => {
    const toc = [node('root', null, '目录'), node('a', 'root', '原条目', 'c1'),
      node('a', 'root', '重复ID条目', 'c2'), node('alias', 'root', '别名', 'c1')]
    const { rerender } = render(<ChapterNavigation bookId="a" chapters={CHAPTERS} toc={toc} activeChapterId="c1" onSelect={vi.fn()} />)
    expect(screen.queryByText('重复ID条目')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /第二章/ })).toBeVisible()
    expect(screen.getByRole('group', { name: /^目录：共 1 章/ })).toHaveTextContent('1 章')
    fireEvent.click(screen.getByRole('button', { name: '收起目录' }))
    rerender(<ChapterNavigation bookId="a" chapters={CHAPTERS.map(chapter => ({ ...chapter, dialogue_processed: true }))}
      toc={toc} activeChapterId="c1" onSelect={vi.fn()} />)
    expect(screen.getByRole('button', { name: '展开目录' })).toHaveAttribute('aria-expanded', 'false')
    expect(within(screen.getByRole('group', { name: /^目录：共 1 章/ })).getByLabelText('已完成 1 章')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '展开目录' }))
    expect(screen.getByText('别名').closest('button')).toHaveAttribute('data-processing-state', 'processed')
  })
  it('shows filtered chapters without changing completion, preserves active work and clears stale skipped states', () => {
    updateGeneralSettings({ chapterFilterEnabled: true })
    const chapters = [{ ...CHAPTERS[0], title: '封面', dialogue_processed: false },
      { ...CHAPTERS[1], title: '彩页', dialogue_processed: false }]
    const select = vi.fn()
    const states = { c1: { state: 'skipped' as const, completedWindows: 0, totalWindows: 0, error: '旧过滤提示' },
      c2: { state: 'roster' as const, completedWindows: 0, totalWindows: 1, pendingTasks: 1, error: null } }
    const page = render(<ChapterNavigation bookId="b1" chapters={chapters} activeChapterId="c1" onSelect={select} processingStates={states} />)
    expect(screen.getByText('封面').closest('button')).toHaveAttribute('data-processing-state', 'skipped')
    expect(screen.getByText('彩页').closest('button')).toHaveAttribute('data-processing-state', 'roster')
    expect(screen.getByRole('button', { name: '取消彩页的任务' })).toBeVisible()
    fireEvent.click(screen.getByText('封面'))
    expect(select).toHaveBeenCalledWith(chapters[0])
    expect(chapters[0].dialogue_processed).toBe(false)
    page.unmount()
    updateGeneralSettings({ chapterFilterEnabled: false })
    render(<ChapterNavigation chapters={chapters} activeChapterId="c1" onSelect={select} processingStates={states} />)
    expect(screen.getByText('封面').closest('button')).toHaveAttribute('data-processing-state', 'unprocessed')
  })
  it('卷目录可折叠，切换当前章自动展开所属卷，保留原章节选择', () => {
    const chapters = CHAPTERS.map((chapter, index) => ({ ...chapter,
      title: `${index < 2 ? '第一卷' : '第二卷'} · ${chapter.title}` }))
    const select = vi.fn()
    const { rerender } = render(<ChapterNavigation chapters={chapters} activeChapterId="c1" onSelect={select} />)
    expect(screen.getByRole('button', { name: '收起第一卷' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: '展开第二卷' })).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(screen.getByText('第二章'))
    expect(select).toHaveBeenCalledWith(chapters[1])
    fireEvent.click(screen.getByRole('button', { name: '收起第一卷' }))
    expect(screen.getByRole('button', { name: '展开第一卷' })).toHaveAttribute('aria-expanded', 'false')
    rerender(<ChapterNavigation chapters={chapters} activeChapterId="c3" onSelect={select} />)
    expect(screen.getByRole('button', { name: '收起第二卷' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('第三章').closest('button')).toHaveAttribute('aria-current', 'true')
  })
  it('收起卷仍显示任务失败摘要，展开后保留章节旁重试操作', () => {
    const chapters = [{ ...CHAPTERS[0], title: '第一卷 · 彩页' },
      { ...CHAPTERS[1], title: '第二卷 · 第一章' }]
    render(<ChapterNavigation bookId="b1" chapters={chapters} activeChapterId="c1" onSelect={vi.fn()}
      processingStates={{ c2: { state: 'failed', completedWindows: 0, totalWindows: 1, error: '测试错误' } }} />)
    expect(screen.getByLabelText('失败/已停止 1 章')).toBeVisible()
    expect(screen.queryByRole('button', { name: '重试第二卷 · 第一章的任务' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '展开第二卷' }))
    expect(screen.getByRole('button', { name: '重试第二卷 · 第一章的任务' })).toBeEnabled()
  })
  it('双击切换默认关闭，不写入章节状态', () => {
    const save = vi.fn()
    render(<ChapterNavigation chapters={CHAPTERS} activeChapterId="c1" onSelect={vi.fn()} onSetProcessingStatus={save} />)
    fireEvent.doubleClick(screen.getByText('第一章'))
    expect(save).not.toHaveBeenCalled()
  })
  it('卷摘要统计后端已完成章节和人工完成状态，不受旧失败干扰', () => {
    const chapters = Array.from({ length: 12 }, (_, index) => ({ ...CHAPTERS[0],
      id: `done-${index}`, ordinal: index, title: `第一卷 · 第${index + 1}章`,
      dialogue_processed: index > 0, processing_status_override: index === 0 ? true : null }))
    render(<ChapterNavigation chapters={chapters} activeChapterId={null} onSelect={vi.fn()}
      processingStates={{ 'done-1': { state: 'failed', completedWindows: 0, totalWindows: 1, error: '旧失败' } }} />)
    const summary = screen.getByRole('group', { name: /^第一卷：共 12 章/ })
    expect(within(summary).getByLabelText('已完成 12 章')).toHaveClass('processed')
    expect(within(summary).getByLabelText('已完成 12 章')).toHaveAttribute('title', '已完成 12 章')
    expect(summary.querySelectorAll('[data-processing-state]')).toHaveLength(1)
    expect(summary).not.toHaveTextContent('章排队/处理中')
    expect(summary).toBeVisible()
  })
  it('卷摘要区分全部实时状态，停止与失败合并，并随更新变动', () => {
    const states = ['unprocessed', 'queued', 'roster', 'dialogue', 'processed', 'failed', 'stopped'] as const
    const chapters = states.map((state, index) => ({ ...CHAPTERS[0],
      id: state, ordinal: index, title: `第一卷 · ${state}`, dialogue_processed: state === 'processed' }))
    const processingStates = Object.fromEntries(states.map(state => [state,
      { state, completedWindows: 0, totalWindows: 1, error: null }]))
    const { rerender } = render(<ChapterNavigation chapters={chapters} activeChapterId={null}
      onSelect={vi.fn()} processingStates={processingStates} />)
    const summary = screen.getByRole('group', { name: /^第一卷：共 7 章/ })
    for (const label of ['未处理 1 章', '排队中 1 章', '正在识别人物 1 章',
      '人物已确认，正在处理对白 1 章', '已完成 1 章', '失败/已停止 2 章']) {
      expect(within(summary).getByLabelText(label)).toBeVisible()
    }
    expect(summary.querySelectorAll('[data-processing-state]')).toHaveLength(6)
    const updated = chapters.map(chapter => ({ ...chapter, dialogue_processed: true }))
    rerender(<ChapterNavigation chapters={updated} activeChapterId={null} onSelect={vi.fn()} />)
    expect(within(summary).getByLabelText('已完成 7 章')).toBeVisible()
    expect(summary.querySelectorAll('[data-processing-state]')).toHaveLength(1)
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
