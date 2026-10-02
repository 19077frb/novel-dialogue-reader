import { useEffect, useRef, useState } from 'react'
import { useGeneralSettings } from '../settings/preferences'
import type { ChapterOut } from '../api/types'
import { cancelChapterProcessing, retryChapterProcessing } from './BatchProcessor'
import type { ChapterProcessingProgress, ChapterProcessingState } from './BatchProcessor'

export interface ChapterNavigationProps {
  bookId?: string
  chapters: ChapterOut[]
  activeChapterId: string | null
  onSelect: (chapter: ChapterOut) => void
  processingStates?: Record<string, ChapterProcessingProgress>
  onSetProcessingStatus?: (chapter: ChapterOut, processed: boolean) => Promise<ChapterOut>
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

export function ChapterNavigation({ bookId, chapters, activeChapterId, onSelect, processingStates = {}, onSetProcessingStatus }: ChapterNavigationProps) {
  const [settings] = useGeneralSettings()
  const selectionTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const statusLocks = useRef(new Set<string>())
  const [manualUpdates, setManualUpdates] = useState<Record<string, { source: ChapterOut; saved: ChapterOut }>>({})
  const canToggle = settings.doubleClickChapterStatus && Boolean(onSetProcessingStatus)
  useEffect(() => () => { if (selectionTimer.current) clearTimeout(selectionTimer.current) }, [bookId, canToggle])
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [error, setError] = useState<string | null>(null)
  const toggleStatus = (chapter: ChapterOut, processed: boolean) => {
    if (selectionTimer.current) clearTimeout(selectionTimer.current)
    if (!canToggle || !onSetProcessingStatus || statusLocks.current.has(chapter.id)) return
    statusLocks.current.add(chapter.id)
    setBusy(current => ({ ...current, [chapter.id]: true })); setError(null)
    void onSetProcessingStatus(chapter, processed).then(saved => {
      setManualUpdates(current => ({ ...current, [chapter.id]: { source: chapter, saved } }))
    }).catch(reason => setError(`${chapter.title ?? '本章'}：${reason instanceof Error ? reason.message : '状态保存失败'}`))
      .finally(() => { statusLocks.current.delete(chapter.id); setBusy(current => ({ ...current, [chapter.id]: false })) })
  }
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
      {canToggle && <p className="hint">双击章节名可切换未处理/已完成，仅改变完成标记。</p>}
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
          {chapters.map((sourceChapter) => {
            const manual = manualUpdates[sourceChapter.id]
            const chapter = manual?.source === sourceChapter ? manual.saved : sourceChapter
            const active = chapter.id === activeChapterId
            const recorded = processingStates[chapter.id]
            const progress = chapter.processing_status_override != null
              ? { state: chapter.processing_status_override ? 'processed' as const : 'unprocessed' as const,
                completedWindows: 0, totalWindows: 0, error: null }
              : chapter.dialogue_processed && !recorded?.cancelRequested && !(recorded?.pendingTasks ?? 0)
              && !['queued', 'roster', 'dialogue'].includes(recorded?.state ?? '')
              ? { state: 'processed' as const, completedWindows: recorded?.totalWindows ?? 0, totalWindows: recorded?.totalWindows ?? 0, error: null }
              : recorded ?? {
              state: chapter.dialogue_processed ? 'processed' as const : 'unprocessed' as const,
              completedWindows: 0,
              totalWindows: 0,
              error: null,
            }
            const cancellable = recorded?.cancelRequested || (recorded?.pendingTasks ?? 0) > 0
              || ['queued', 'roster', 'dialogue'].includes(recorded?.state ?? '')
              || progress.cancelRequested || (progress.pendingTasks ?? 0) > 0 || ['queued', 'roster', 'dialogue'].includes(progress.state)
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
                  disabled={Boolean(busy[chapter.id]) && statusLocks.current.has(chapter.id)}
                  onClick={event => {
                    if (!canToggle || event.detail === 0) { onSelect(chapter); return }
                    if (selectionTimer.current) clearTimeout(selectionTimer.current)
                    selectionTimer.current = setTimeout(() => onSelect(chapter), 500)
                  }}
                >
                  <span className="ndr-chapter-title" onDoubleClick={event => {
                    event.stopPropagation(); toggleStatus(sourceChapter, progress.state !== 'processed')
                  }}>{chapter.title ?? `第 ${chapter.ordinal + 1} 节`}</span>
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
