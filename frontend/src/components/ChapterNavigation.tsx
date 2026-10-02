import { useEffect, useRef, useState } from 'react'
import { useGeneralSettings } from '../settings/preferences'
import type { ChapterOut } from '../api/types'
import { cancelChapterProcessing, retryChapterProcessing } from './BatchProcessor'
import type { ChapterProcessingProgress, ChapterProcessingState } from './BatchProcessor'
import { CollapsibleBlock } from './CollapsibleBlock'

function chapterGroups(chapters: ChapterOut[]) {
  const groups: { key: string; volume: string | null; chapters: ChapterOut[] }[] = []
  for (const chapter of chapters) {
    const separator = chapter.title?.indexOf(' · ') ?? -1
    const volume = separator > 0 ? chapter.title!.slice(0, separator) : null
    const previous = groups[groups.length - 1]
    if (previous && previous.volume === volume) previous.chapters.push(chapter)
    else groups.push({ key: chapter.id, volume, chapters: [chapter] })
  }
  return groups
}

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

const SUMMARY_STATES = ['unprocessed', 'queued', 'roster', 'dialogue', 'processed', 'failed'] as const

function VolumeProgressSummary({ volume, chapters, progressOf }: {
  volume: string
  chapters: ChapterOut[]
  progressOf: (chapter: ChapterOut) => ChapterProcessingProgress
}) {
  const counts = { unprocessed: 0, queued: 0, roster: 0, dialogue: 0, processed: 0, failed: 0 }
  for (const chapter of chapters) {
    const state = progressOf(chapter).state
    counts[state === 'stopped' ? 'failed' : state] += 1
  }
  const labelOf = (state: typeof SUMMARY_STATES[number]) => state === 'failed' ? '失败/已停止' : STATE_LABELS[state]
  const summary = `共 ${chapters.length} 章；${SUMMARY_STATES.map(state => `${labelOf(state)} ${counts[state]} 章`).join('；')}`
  return <div className="ndr-volume-status" role="group" aria-label={`${volume}：${summary}`} title={summary}>
    <span className="ndr-volume-total">{chapters.length} 章</span>
    {SUMMARY_STATES.filter(state => counts[state] > 0).map(state => (
      <span key={state} className={`ndr-volume-count ${state}`} data-processing-state={state}
        aria-label={`${labelOf(state)} ${counts[state]} 章`} title={`${labelOf(state)} ${counts[state]} 章`}>
        {counts[state]}
      </span>
    ))}
  </div>
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
  const groups = chapterGroups(chapters)
  const activeGroup = groups.find(group => group.chapters.some(chapter => chapter.id === activeChapterId))?.key
  const [openVolumes, setOpenVolumes] = useState<Record<string, boolean>>({})
  useEffect(() => {
    if (activeGroup) setOpenVolumes(current => ({ ...current, [activeGroup]: true }))
  }, [activeGroup, activeChapterId])
  const effectiveProgress = (chapter: ChapterOut): ChapterProcessingProgress => {
    const manual = manualUpdates[chapter.id]
    chapter = manual?.source === chapter ? manual.saved : chapter
    const recorded = processingStates[chapter.id]
    const manualStatus = chapter.processing_status_override === true
      || chapter.processing_status_override === false && !recorded?.manualStatusCleared
    return manualStatus
      ? { state: chapter.processing_status_override ? 'processed' as const : 'unprocessed' as const,
        completedWindows: 0, totalWindows: 0, error: null }
      : chapter.dialogue_processed && !recorded?.cancelRequested && !(recorded?.pendingTasks ?? 0)
      && !['queued', 'roster', 'dialogue'].includes(recorded?.state ?? '')
      ? { state: 'processed' as const, completedWindows: recorded?.totalWindows ?? 0, totalWindows: recorded?.totalWindows ?? 0, error: null }
      : recorded ?? {
      state: chapter.dialogue_processed ? 'processed' as const : 'unprocessed' as const,
      completedWindows: 0, totalWindows: 0, error: null,
    }
  }
  const renderChapters = (items: ChapterOut[], volume: string | null) => <ol>
          {items.map((sourceChapter) => {
            const manual = manualUpdates[sourceChapter.id]
            const chapter = manual?.source === sourceChapter ? manual.saved : sourceChapter
            const active = chapter.id === activeChapterId
            const recorded = processingStates[chapter.id]
            const manualStatus = chapter.processing_status_override === true
              || chapter.processing_status_override === false && !recorded?.manualStatusCleared
            const progress = effectiveProgress(chapter)
            const cancellable = !manualStatus && (recorded?.cancelRequested || (recorded?.pendingTasks ?? 0) > 0
              || ['queued', 'roster', 'dialogue'].includes(recorded?.state ?? '')
              || progress.cancelRequested || (progress.pendingTasks ?? 0) > 0 || ['queued', 'roster', 'dialogue'].includes(progress.state))
            const retryable = !cancellable && ['failed', 'stopped'].includes(progress.state)
            const label = `${cancellable ? '取消' : '重试'}${chapter.title ?? `第 ${chapter.ordinal + 1} 节`}的任务`
            const displayTitle = volume ? chapter.title?.slice(volume.length + 3) : chapter.title
            return (
              <li key={chapter.id} className="ndr-chapter-row">
                <button type="button"
                  className={`ndr-chapter ndr-chapter-${progress.state}${active ? ' active' : ''}`}
                  data-processing-state={progress.state}
                  title={Boolean(busy[chapter.id]) && statusLocks.current.has(chapter.id) ? '正在保存章节完成状态，请等待保存完成后再操作。' : progress.error ?? undefined}
                  aria-current={active ? 'true' : undefined}
                  disabled={Boolean(busy[chapter.id]) && statusLocks.current.has(chapter.id)}
                  onClick={event => {
                    if (!canToggle || event.detail === 0) { onSelect(chapter); return }
                    if (selectionTimer.current) clearTimeout(selectionTimer.current)
                    selectionTimer.current = setTimeout(() => onSelect(chapter), 500)
                  }}>
                  <span className="ndr-chapter-title" onDoubleClick={event => {
                    event.stopPropagation(); toggleStatus(sourceChapter, progress.state !== 'processed')
                  }}>{displayTitle ?? `第 ${chapter.ordinal + 1} 节`}</span>
                  <span className="ndr-chapter-range">{progressLabel(progress)}</span>
                </button>
                {bookId && (cancellable || retryable) && <button type="button"
                  className={`ndr-chapter-action${cancellable ? ' ndr-danger' : ''}`}
                  title={progress.cancelRequested ? '正在取消本章，请等待在途请求收尾。' : busy[chapter.id] ? '正在提交本章操作，请等待完成后再重试或取消。' : label}
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
      {canToggle && <p className="hint">双击切换未处理/已完成，并撤销本章旧队列；未处理章节可重新自动入队。</p>}
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
          {groups.map(group => group.volume ? (
            <CollapsibleBlock key={group.key} title={group.volume}
              summary={<VolumeProgressSummary volume={group.volume} chapters={group.chapters} progressOf={effectiveProgress} />}
              open={openVolumes[group.key] ?? group.key === activeGroup}
              onOpenChange={open => setOpenVolumes(current => ({ ...current, [group.key]: open }))}>
              {renderChapters(group.chapters, group.volume)}
            </CollapsibleBlock>
          ) : <div key={group.key}>{renderChapters(group.chapters, null)}</div>)}
        </>
      )}
    </nav>
  )
}
