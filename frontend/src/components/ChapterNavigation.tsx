import type { ChapterOut } from '../api/types'
import type { ChapterProcessingProgress, ChapterProcessingState } from './BatchProcessor'

export interface ChapterNavigationProps {
  chapters: ChapterOut[]
  activeChapterId: string | null
  onSelect: (chapter: ChapterOut) => void
  processingStates?: Record<string, ChapterProcessingProgress>
}

const STATE_LABELS: Record<ChapterProcessingState, string> = {
  unprocessed: '未处理',
  queued: '排队中',
  roster: '正在识别人物',
  dialogue: '人物已确认，正在处理对白',
  processed: '已完成',
  failed: '失败',
  stopped: '已停止',
}

function progressLabel(progress: ChapterProcessingProgress): string {
  if (progress.state === 'dialogue') {
    return `${STATE_LABELS.dialogue} ${progress.completedWindows}/${progress.totalWindows}`
  }
  return STATE_LABELS[progress.state]
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
            <span className="queued">排队中</span>
            <span className="roster">识别人物</span>
            <span className="dialogue">处理对白</span>
            <span className="processed">已完成</span>
            <span className="failed">失败/已停止</span>
          </p>
          <ol>
          {chapters.map((chapter) => {
            const active = chapter.id === activeChapterId
            const progress = processingStates[chapter.id] ?? {
              state: chapter.dialogue_processed ? 'processed' as const : 'unprocessed' as const,
              completedWindows: 0,
              totalWindows: 0,
              error: null,
            }
            return (
              <li key={chapter.id}>
                <button
                  type="button"
                  className={`ndr-chapter ndr-chapter-${progress.state}${active ? ' active' : ''}`}
                  data-processing-state={progress.state}
                  title={progress.error ?? undefined}
                  aria-current={active ? 'true' : undefined}
                  onClick={() => onSelect(chapter)}
                >
                  <span className="ndr-chapter-title">{chapter.title ?? `第 ${chapter.ordinal + 1} 节`}</span>
                  <span className="ndr-chapter-range">
                    {progressLabel(progress)}
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
