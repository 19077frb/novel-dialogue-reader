import type { ChapterOut } from '../api/types'
import type { ChapterProcessingState } from './BatchProcessor'

export interface ChapterNavigationProps {
  chapters: ChapterOut[]
  activeChapterId: string | null
  onSelect: (chapter: ChapterOut) => void
  processingStates?: Record<string, ChapterProcessingState>
}

const STATE_LABELS: Record<ChapterProcessingState, string> = {
  unprocessed: '未处理',
  processing: '处理中',
  processed: '已处理',
}

export function ChapterNavigation({ chapters, activeChapterId, onSelect, processingStates = {} }: ChapterNavigationProps) {
  return (
    <nav className="ndr-chapter-nav" aria-label="章节导航">
      <h2>目录</h2>
      {chapters.length === 0 ? (
        <p className="hint">这本书还没有章节。</p>
      ) : (
        <>
          <p className="ndr-chapter-status-legend" data-testid="chapter-status-legend">
            <span className="unprocessed">未处理</span>
            <span className="processing">处理中</span>
            <span className="processed">已处理</span>
          </p>
          <ol>
          {chapters.map((chapter) => {
            const active = chapter.id === activeChapterId
            const processingState = processingStates[chapter.id]
              ?? (chapter.dialogue_processed ? 'processed' : 'unprocessed')
            return (
              <li key={chapter.id}>
                <button
                  type="button"
                  className={`ndr-chapter ndr-chapter-${processingState}${active ? ' active' : ''}`}
                  data-processing-state={processingState}
                  aria-current={active ? 'true' : undefined}
                  onClick={() => onSelect(chapter)}
                >
                  <span className="ndr-chapter-title">{chapter.title ?? `第 ${chapter.ordinal + 1} 节`}</span>
                  <span className="ndr-chapter-range">
                    {STATE_LABELS[processingState]}
                  </span>
                </button>
              </li>
            )
          })}
          </ol>
        </>
      )}
    </nav>
  )
}
