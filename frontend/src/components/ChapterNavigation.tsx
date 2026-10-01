import { useState } from 'react'
import type { ChapterOut } from '../api/types'
import { cancelChapterProcessing, retryChapterProcessing } from './BatchProcessor'
import type { ChapterProcessingProgress, ChapterProcessingState } from './BatchProcessor'

export interface ChapterNavigationProps {
  bookId?: string
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
  if (progress.cancelRequested) return '正在取消，等待请求收尾…'
  if (progress.state === 'dialogue') {
    return `${STATE_LABELS.dialogue} ${progress.completedWindows}/${progress.totalWindows}`
  }
  return STATE_LABELS[progress.state]
}

export function ChapterNavigation({ bookId, chapters, activeChapterId, onSelect, processingStates = {} }: ChapterNavigationProps) {
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const act = (chapter: ChapterOut, cancel: boolean) => {
    if (!bookId) return
    setBusy(current => ({ ...current, [chapter.id]: true })); setError(null)
    void (cancel ? cancelChapterProcessing(bookId, chapter.id) : retryChapterProcessing(bookId, chapter.id))
      .catch(reason => setError(`${chapter.title ?? '本章'}：${reason instanceof Error ? reason.message : '操作失败'}`))
      .finally(() => setBusy(current => ({ ...current, [chapter.id]: false })))
  }
  return (
    <nav className="ndr-chapter-nav" aria-label="章节导航">
      {error && <p className="status-error" role="alert">{error}</p>}
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
            const recorded = processingStates[chapter.id]
            const progress = chapter.dialogue_processed && !recorded?.cancelRequested && !(recorded?.pendingTasks ?? 0)
              && !['queued', 'roster', 'dialogue'].includes(recorded?.state ?? '')
              ? { state: 'processed' as const, completedWindows: recorded?.totalWindows ?? 0, totalWindows: recorded?.totalWindows ?? 0, error: null }
              : recorded ?? {
              state: chapter.dialogue_processed ? 'processed' as const : 'unprocessed' as const,
              completedWindows: 0,
              totalWindows: 0,
              error: null,
            }
            const cancellable = progress.cancelRequested || (progress.pendingTasks ?? 0) > 0 || ['queued', 'roster', 'dialogue'].includes(progress.state)
            const retryable = !cancellable && ['failed', 'stopped'].includes(progress.state)
            const label = `${cancellable ? '取消' : '重试'}${chapter.title ?? `第 ${chapter.ordinal + 1} 节`}的任务`
            return (
              <li key={chapter.id} className="ndr-chapter-row">
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
                {bookId && (cancellable || retryable) && <button type="button"
                  className={`ndr-chapter-action${cancellable ? ' ndr-danger' : ''}`}
                  title={progress.cancelRequested ? '正在取消本章，等待在途请求收尾' : label}
                  aria-label={label} disabled={busy[chapter.id] || progress.cancelRequested}
                  onClick={() => act(chapter, Boolean(cancellable))}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                    {cancellable ? <path d="M6 6l12 12M18 6L6 18" /> : <><path d="M20 7v5h-5" /><path d="M20 12a8 8 0 1 0-2 5M20 7l-4 4" /></>}
                  </svg>
                </button>}
              </li>
            )
          })}
          </ol>
        </>
      )}
    </nav>
  )
}
