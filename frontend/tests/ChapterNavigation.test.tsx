import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ChapterNavigation } from '../src/components/ChapterNavigation'
import type { ChapterOut } from '../src/api/types'

const CHAPTERS = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 10, source_href: null },
  { id: 'c2', ordinal: 1, title: '第二章', start_cp: 10, end_cp: 20, source_href: null },
  { id: 'c3', ordinal: 2, title: '第三章', start_cp: 20, end_cp: 30, source_href: null },
] as ChapterOut[]

describe('ChapterNavigation', () => {
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
