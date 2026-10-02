import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { bookmarkKey, deleteBookmark, editBookmark, fetchBookmarks } from '../api/bookmarks'
import type { BookmarkOut } from '../api/types'
import { ReadErrorNotice } from './ReadErrorNotice'
import { CollapsibleBlock } from './CollapsibleBlock'
import { DisabledHint } from './DisabledHint'

function BookmarkCard({ item, activeVersionId, saved }: { item: BookmarkOut; activeVersionId?: string | null; saved: () => Promise<void> }) {
  const [note, setNote] = useState(item.note)
  const mutation = useMutation({
    mutationFn: async (remove: boolean) => { if (remove) await deleteBookmark(item.book_id, item); else await editBookmark(item.book_id, item, note) },
    onSuccess: saved,
  })
  return <article className="ndr-bookmark-card" data-testid="bookmark-card">
    <h3>{item.chapter_title}</h3>
    <blockquote>{item.excerpt.split(/\r?\n/u)[0].trim()}</blockquote>
    {item.note && <p className="ndr-bookmark-note">{item.note}</p>}
    <time className="hint" dateTime={item.created_at}>{new Date(item.created_at).toLocaleString('zh-CN')}</time>
    <div className="ndr-form-actions">
      {item.book_version_id === activeVersionId ? <Link className="ndr-button ndr-primary" to={`/books/${item.book_id}/read?chapterId=${item.chapter_id}&positionCp=${item.position_cp}`}>跳转阅读</Link> : <span className="hint">原文版本已更换，暂不能跳转。</span>}
      <button title={mutation.isPending ? '正在保存或删除书签，请等待完成。' : undefined} className="ndr-danger" disabled={mutation.isPending} onClick={() => { if (window.confirm('删除这个书签？不会删除正文或阅读进度。')) mutation.mutate(true) }}>删除书签</button>
    </div>
    <details className="ndr-bookmark-edit">
      <summary>编辑备注</summary>
    <label className="ndr-field">书签备注<textarea value={note} maxLength={512} onChange={e => setNote(e.target.value)} /></label>
    <div className="ndr-form-actions">
      <button title={mutation.isPending ? '正在保存或删除书签，请等待完成。' : note === item.note ? '备注尚未修改，请先编辑备注再保存。' : undefined} className="ndr-primary" disabled={mutation.isPending || note === item.note} onClick={() => mutation.mutate(false)}>保存备注</button>
    </div>
    <DisabledHint reason={mutation.isPending ? '正在保存或删除书签，请等待完成。' : note === item.note && '备注尚未修改，请先编辑备注再保存。'} />
    </details>
    {mutation.error && <p className="status-error" role="alert">{mutation.error.message}；请重新读取后重试。</p>}
  </article>
}

export function BookmarkList({ bookId, activeVersionId }: { bookId: string; activeVersionId?: string | null }) {
  const client = useQueryClient()
  const list = useInfiniteQuery({ queryKey: bookmarkKey(bookId), initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => fetchBookmarks(bookId, pageParam, signal), getNextPageParam: page => page.next_cursor ?? undefined })
  const items = (list.data?.pages.flatMap(page => page.items) ?? []).slice().sort((a, b) => a.position_cp - b.position_cp)
  return <div>
    <button title={list.isFetching ? '正在读取书签，请等待完成后再刷新。' : undefined} onClick={() => void list.refetch()} disabled={list.isFetching}>重新读取书签</button>
    {list.isPending && <p>正在读取书签…</p>}
    {list.isError && <ReadErrorNotice label="书签读取失败" error={list.error} retrying={list.isFetching} onRetry={() => void list.refetch()} />}
    {!list.isPending && !list.isError && !items.length && <p className="hint">还没有书签。阅读时点击段落旁的 ☆，收藏想再看的段落。</p>}
    <CollapsibleBlock title="书签列表" summary={`已加载 ${items.length} 个书签`}>
    <div className="ndr-bookmark-list">{items.map(item => <BookmarkCard key={`${item.id}:${item.version}`} item={item} activeVersionId={activeVersionId} saved={() => client.invalidateQueries({ queryKey: bookmarkKey(bookId) })} />)}</div>
    {list.hasNextPage && <button title={list.isFetching ? '正在读取书签，请等待本次读取完成。' : undefined} disabled={list.isFetching} onClick={() => void list.fetchNextPage()}>加载更多书签</button>}
    </CollapsibleBlock>
  </div>
}
