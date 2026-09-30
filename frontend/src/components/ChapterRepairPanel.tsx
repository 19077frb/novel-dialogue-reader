import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { fetchChapters, fetchChapterRepairs, queryKeys, repairChapters } from '../api/books'
import { ReadErrorNotice } from './ReadErrorNotice'
import { isBatchRunning } from './BatchProcessor'

export function ChapterRepairPanel({ bookId, bookVersionId }: { bookId: string; bookVersionId?: string | null }) {
  const client = useQueryClient()
  const chapters = useQuery({ queryKey: queryKeys.chapters(bookId), queryFn: ({ signal }) => fetchChapters(bookId, signal) })
  const suggestions = useQuery({ queryKey: ['chapter-repairs', bookId], queryFn: ({ signal }) => fetchChapterRepairs(bookId, signal) })
  const [drafts, setDrafts] = useState<Record<string, { title: string; merge: boolean }>>({})
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  async function save(id: string, title: string | null) {
    const draft = drafts[id]
    if (!bookVersionId || !draft) return
    if (isBatchRunning(bookId)) { setError('请先停止本书批量或自动处理，再修复目录。'); return }
    if (draft.merge && !window.confirm('将本章并入上一章？仅调整目录边界，不删除正文；无法自动撤销。')) return
    setBusy(true); setError(''); setNotice('')
    try {
      await repairChapters(bookId, bookVersionId, [{ chapter_id: id, expected_title: title, title: draft.title, merge_previous: draft.merge }])
      setDrafts({})
      await Promise.all([
        client.invalidateQueries({ queryKey: queryKeys.chapters(bookId) }),
        client.invalidateQueries({ queryKey: queryKeys.book(bookId) }),
        client.invalidateQueries({ queryKey: ['chapter-repairs', bookId] }),
        client.invalidateQueries({ queryKey: ['content', bookId] }),
        client.invalidateQueries({ queryKey: ['quotes', bookId] }),
      ])
      setNotice('章节修复已保存，原文和阅读位置未改变。')
    } catch (reason) { setError(reason instanceof Error ? reason.message : '章节修复失败') }
    finally { setBusy(false) }
  }
  return <section className="card" data-testid="chapter-repair-panel">
    <h3>章节名与边界修复</h3>
    <p className="hint">自动提示正文式标题与重复序章；建议仅供检查，不调用模型。可改名，或将误识别的章节并入上一章。已有人物名单或标注的章节只能改名。</p>
    {chapters.isError && <ReadErrorNotice label="目录读取失败" error={chapters.error} retrying={chapters.isFetching} onRetry={() => void chapters.refetch()} />}
    {suggestions.isError && <ReadErrorNotice label="章节建议读取失败" error={suggestions.error} retrying={suggestions.isFetching} onRetry={() => void suggestions.refetch()} />}
    {chapters.isPending && <p className="hint">正在读取目录…</p>}
    {notice && <p role="status">{notice}</p>}{error && <p className="status-error" role="alert">{error}</p>}
    {(chapters.data ?? []).map((chapter, index) => {
      const suggestion = suggestions.data?.find(item => item.chapter_id === chapter.id)
      const draft = drafts[chapter.id] ?? { title: chapter.title ?? '', merge: false }
      const change = (patch: Partial<typeof draft>) => setDrafts(current => ({ ...current, [chapter.id]: { ...draft, ...patch } }))
      return <details className="card" key={chapter.id}>
        <summary>{chapter.title || `第 ${index + 1} 节`}{suggestion ? ' · 建议检查' : ''}</summary>
        {suggestion && <p className="hint">{suggestion.reason}<button disabled={busy} onClick={() => change({ title: suggestion.suggested_title, merge: suggestion.merge_previous })}>采用建议（待保存）</button></p>}
        <label className="ndr-field">章节名<input value={draft.title} maxLength={512} disabled={busy} onChange={event => change({ title: event.target.value })} /></label>
        <label><input type="checkbox" checked={draft.merge} disabled={busy || index === 0 || chapter.dialogue_processed} onChange={event => change({ merge: event.target.checked })} />并入上一章（以上方名称命名合并后的章节）</label>
        <button className="ndr-primary" disabled={busy || !bookVersionId || !drafts[chapter.id] || !draft.title.trim()} onClick={() => void save(chapter.id, chapter.title ?? null)}>保存章节修复</button>
      </details>
    })}
  </section>
}
