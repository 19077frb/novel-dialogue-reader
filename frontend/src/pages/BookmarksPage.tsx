import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { fetchBook, queryKeys } from '../api/books'
import { BookmarkList } from '../components/BookmarkList'

export default function BookmarksPage() {
  const { bookId = '' } = useParams()
  const book = useQuery({ queryKey: queryKeys.book(bookId), queryFn: ({ signal }) => fetchBook(bookId, signal) })
  return <div className="ndr-page">
    <header className="card ndr-page-header ndr-reader-header"><div><h2>书签：{book.data?.title ?? '当前书籍'}</h2><p className="hint">保存想再看的段落和备注，与最后阅读位置独立。</p></div>
      <nav className="ndr-book-nav" aria-label="本书导航"><Link to={`/books/${bookId}/read`}>去阅读</Link><Link to={`/books/${bookId}/preview`}>预览与处理</Link><Link to={`/books/${bookId}/characters`}>全书人物</Link><Link to={`/books/${bookId}/review`}>待确认队列</Link><Link to="/library">返回书架</Link></nav>
    </header><section className="card"><BookmarkList bookId={bookId} activeVersionId={book.data?.active_version_id} /></section>
  </div>
}
