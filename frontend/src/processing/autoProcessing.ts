import { fetchChapters, fetchProcessingStatus } from '../api/books'
import { estimateRange } from '../api/jobs'
import type { ProcessingPreferences } from './preferences'
import { mapWithConcurrency } from './concurrency'
import { appendAutomaticProcessing, automaticAllowance, refreshAutomaticAllowance, canAppendAutomaticProcessing, hasBatchWork, hasUnresolvedChapterResult, isBatchRunning, requestBatchStop, runBatchProcessing } from '../components/BatchProcessor'

interface AutoSession { spent: number; attempted: Set<string>; blocked: boolean; message: string; checked: string; revision: number }
const sessions = new Map<string, AutoSession>()
const planningBooks = new Map<string, symbol>()
let activeBook: string | null = null
function sessionFor(bookId: string) {
  if (!sessions.has(bookId)) sessions.set(bookId, { spent: 0, attempted: new Set(), blocked: false, message: '', checked: '', revision: 0 })
  return sessions.get(bookId)!
}
export function autoMessage(bookId: string) { return sessionFor(bookId).message }
export function autoBusy(bookId: string) { return activeBook === bookId }
export function notifyManualChapterStatus(bookId: string, versionId: string, chapterId: string, processed: boolean) {
  const session = sessionFor(bookId)
  const key = `${versionId}:${chapterId}`
  if (processed) session.attempted.add(key)
  else session.attempted.delete(key)
  session.checked = ''
  session.revision += 1
}
export function resetAutomaticProcessing(bookId: string) {
  if (activeBook === bookId) return
  sessions.delete(bookId)
  refreshAutomaticAllowance(bookId)
}
export function stopAutomaticProcessing() {
  if (!activeBook) return
  sessionFor(activeBook).blocked = true
  sessionFor(activeBook).message = '自动处理已停止；已发送的请求会完成后退出。'
  if (isBatchRunning(activeBook)) requestBatchStop(activeBook)
}

/** Called only by an explicitly enabled reader; no retries of failed paid calls. */
export async function scheduleAutomaticProcessing(bookId: string, bookVersionId: string, chapterId: string,
  lookAhead: number, preferences: ProcessingPreferences, onFinished: () => void = () => undefined) {
  const session = sessionFor(bookId)
  session.spent = automaticAllowance(bookId) ?? session.spent
  const appending = canAppendAutomaticProcessing(bookId, bookVersionId)
  if (planningBooks.has(bookId) || (activeBook && !appending) || (hasBatchWork() && !appending)
    || session.blocked || !preferences.profileId) return
  const revision = session.revision
  const signature = JSON.stringify([bookVersionId, chapterId, lookAhead, preferences, revision])
  if (session.checked === signature) return
  const planningToken = Symbol('automatic-estimate')
  planningBooks.set(bookId, planningToken)
  const releasePlanning = () => { if (planningBooks.get(bookId) === planningToken) planningBooks.delete(bookId) }
  let ownsActiveBook = !appending
  if (ownsActiveBook) activeBook = bookId
  try {
    if (!appending) {
      const status = await fetchProcessingStatus(bookId)
      if (status.active_jobs > 0) { session.message = '等待当前任务结束后自动处理。'; return }
    }
    if (preferences.tokenLimit !== null && session.spent >= preferences.tokenLimit) {
      session.blocked = true; session.message = '自动处理额度已用完，请调整或刷新额度后重试。'; return
    }
    const chapters = await fetchChapters(bookId)
    if (revision !== session.revision) return
    const index = chapters.findIndex(chapter => chapter.id === chapterId)
    if (index < 0) return
    const requested = chapters.slice(index, index + Math.max(0, Math.min(100, lookAhead)) + 1)
    const selected = requested.filter(chapter => {
      if (chapter.dialogue_processed || session.attempted.has(`${bookVersionId}:${chapter.id}`)) return false
      if (hasUnresolvedChapterResult(bookId, bookVersionId, chapter.id)) {
        session.message = '本章请求结果尚不明确，请在预览与处理中查看任务详情后恢复。'
        return false
      }
      return true
    })
    if (selected.length === 0) { session.checked = signature; return }
    session.message = '正在估算当前章与后续章节（不消耗模型额度）…'
    const plans = await mapWithConcurrency(selected, 4, async chapter => ({ chapter,
      estimate: await estimateRange(bookId, { bookVersionId,
        range: { chapterId: chapter.id, startCp: chapter.start_cp, endCp: chapter.end_cp },
        readingMode: 'reread', budget: { maxInputTokens: null, maxOutputTokens: null, maxRecheckRounds: preferences.maxRecheckRounds } }) }))
    if (session.blocked || revision !== session.revision) return
    if (canAppendAutomaticProcessing(bookId, bookVersionId)) {
      let added = 0
      for (const plan of plans) {
        if (appendAutomaticProcessing(bookId, bookVersionId, [plan])) {
          added += 1
          session.attempted.add(`${bookVersionId}:${plan.chapter.id}`)
        }
      }
      if (added === plans.length) session.checked = signature
      if (added) session.message = `已根据当前阅读位置追加 ${added} 章，等待空闲位置处理。`
      return
    }
    if (hasBatchWork() || (activeBook && activeBook !== bookId)) return
    if ((await fetchProcessingStatus(bookId)).active_jobs > 0) return
    if (revision !== session.revision) return
    if (!ownsActiveBook) {
      if (activeBook) return
      ownsActiveBook = true
      activeBook = bookId
    }
    selected.forEach(chapter => session.attempted.add(`${bookVersionId}:${chapter.id}`))
    session.checked = signature
    const estimated = plans.reduce((sum, plan) => sum + plan.estimate.total_tokens + plan.chapter.end_cp - plan.chapter.start_cp + 2000, 0)
    session.message = `自动处理 ${selected.length} 章，预计约 ${estimated.toLocaleString()} Tokens。`
    // Release local-estimate admission while the shared model pool is running.
    releasePlanning()
    await runBatchProcessing({ bookId, bookVersionId, requested, plans, preferences, expandable: true,
      initialSpent: session.spent, onUsage: spent => { session.spent = spent },
      onError: message => { session.blocked = Boolean(message); if (message) session.message = message },
      onProgress: message => { session.message = message }, onFinished })
  } catch (reason) {
    session.blocked = true
    session.message = reason instanceof Error ? reason.message : '自动处理失败，请重试。'
  } finally {
    releasePlanning()
    if (ownsActiveBook) activeBook = null
  }
}
