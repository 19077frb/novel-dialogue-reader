import { useQuery } from '@tanstack/react-query'
import { Link, useParams, useSearchParams } from 'react-router-dom'

import { fetchBook, queryKeys } from '../api/books'
import { QuoteNormalizationPanel } from '../components/QuoteNormalizationPanel'
import { ReadErrorNotice } from '../components/ReadErrorNotice'
import { ChapterRepairPanel } from '../components/ChapterRepairPanel'

export default function PreprocessingPage() {
  const { bookId = '' } = useParams()
  const [params] = useSearchParams()
  const chapterId = params.get('chapterId')
  const suffix = chapterId ? `?chapterId=${encodeURIComponent(chapterId)}` : ''
  const book = useQuery({ queryKey: queryKeys.book(bookId), queryFn: ({ signal }) => fetchBook(bookId, signal) })
  return <div className="ndr-page">
    <header className="card ndr-page-header ndr-reader-header"><div><h2>预处理：{book.data?.title ?? '当前书籍'}</h2><p className="hint">按需检查原文的结构问题；修复只调整识别边界，不改写原文。</p></div>
      <nav className="ndr-book-nav" aria-label="本书导航"><Link to={`/books/${bookId}/read${suffix}`}>去阅读</Link><Link to={`/books/${bookId}/preview${suffix}`}>预览与处理</Link><Link to={`/books/${bookId}/characters${suffix}`}>全书人物</Link><Link to={`/books/${bookId}/review`}>待确认队列</Link><Link to="/library">返回书架</Link></nav>
    </header>
    {book.isError && <ReadErrorNotice label="书籍读取失败" error={book.error} retrying={book.isFetching} onRetry={() => void book.refetch()} />}
    <QuoteNormalizationPanel bookId={bookId} />
    <ChapterRepairPanel bookId={bookId} bookVersionId={book.data?.active_version_id} />
  </div>
}
