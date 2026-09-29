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
  it('用不同状态和颜色类实时标记章节处理进度', () => {
    render(
      <ChapterNavigation
        chapters={CHAPTERS}
        activeChapterId="c2"
        onSelect={vi.fn()}
        processingStates={{ c1: 'processed', c2: 'processing', c3: 'unprocessed' }}
      />,
    )

    expect(screen.getByTestId('chapter-status-legend')).toHaveTextContent('未处理处理中已处理')
    expect(screen.getByRole('button', { name: /第一章/ })).toHaveAttribute('data-processing-state', 'processed')
    expect(screen.getByRole('button', { name: /第二章/ })).toHaveAttribute('data-processing-state', 'processing')
    expect(screen.getByRole('button', { name: /第三章/ })).toHaveAttribute('data-processing-state', 'unprocessed')
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
