import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { bookmarkKey, deleteBookmark, editBookmark, fetchBookmarks } from '../api/bookmarks'
import type { BookmarkOut } from '../api/types'
import { ReadErrorNotice } from './ReadErrorNotice'

function BookmarkCard({ item, activeVersionId, saved }: { item: BookmarkOut; activeVersionId?: string | null; saved: () => Promise<void> }) {
  const [note, setNote] = useState(item.note)
  const mutation = useMutation({
    mutationFn: async (remove: boolean) => { if (remove) await deleteBookmark(item.book_id, item); else await editBookmark(item.book_id, item, note) },
    onSuccess: saved,
  })
  return <article className="ndr-character-card" data-testid="bookmark-card">
    <h3>{item.chapter_title}</h3>
    <blockquote>{item.excerpt}</blockquote>
    <p className="hint">{new Date(item.created_at).toLocaleString('zh-CN')}</p>
    <label className="ndr-field">书签备注<textarea value={note} maxLength={512} onChange={e => setNote(e.target.value)} /></label>
    <div className="ndr-form-actions">
      {item.book_version_id === activeVersionId ? <Link className="ndr-button" to={`/books/${item.book_id}/read?chapterId=${item.chapter_id}&positionCp=${item.position_cp}`}>跳转阅读</Link> : <span className="hint">原文版本已更换，暂不能跳转。</span>}
      <button className="ndr-primary" disabled={mutation.isPending || note === item.note} onClick={() => mutation.mutate(false)}>保存备注</button>
      <button className="ndr-danger" disabled={mutation.isPending} onClick={() => { if (window.confirm('删除这个书签？不会删除正文或阅读进度。')) mutation.mutate(true) }}>删除书签</button>
    </div>
    {mutation.error && <p className="status-error" role="alert">{mutation.error.message}；请重新读取后重试。</p>}
  </article>
}

export function BookmarkList({ bookId, activeVersionId }: { bookId: string; activeVersionId?: string | null }) {
  const client = useQueryClient()
  const list = useInfiniteQuery({ queryKey: bookmarkKey(bookId), initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => fetchBookmarks(bookId, pageParam, signal), getNextPageParam: page => page.next_cursor ?? undefined })
  const items = (list.data?.pages.flatMap(page => page.items) ?? []).slice().sort((a, b) => a.position_cp - b.position_cp)
  return <div>
    <button onClick={() => void list.refetch()} disabled={list.isFetching}>重新读取书签</button>
    {list.isPending && <p>正在读取书签…</p>}
    {list.isError && <ReadErrorNotice label="书签读取失败" error={list.error} retrying={list.isFetching} onRetry={() => void list.refetch()} />}
    {!list.isPending && !list.isError && !items.length && <p className="hint">还没有书签，请在阅读时点击“添加书签”。</p>}
    <div className="ndr-character-list">{items.map(item => <BookmarkCard key={`${item.id}:${item.version}`} item={item} activeVersionId={activeVersionId} saved={() => client.invalidateQueries({ queryKey: bookmarkKey(bookId) })} />)}</div>
    {list.hasNextPage && <button disabled={list.isFetching} onClick={() => void list.fetchNextPage()}>加载更多书签</button>}
  </div>
}
