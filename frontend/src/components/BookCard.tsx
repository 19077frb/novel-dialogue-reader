import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BookOpen, ArrowUpRight, Trash2 } from 'lucide-react'
import { Button } from './ui/button'
import { Badge } from './ui/badge'
import { Card, CardHeader, CardContent, CardFooter } from './ui/card'
import '../styles/library.css'

import type { BookOut } from '../api/types'
import { resourceUrl } from '../api/books'

function statusLabel(book: BookOut): string {
  switch (book.import_status) {
    case 'COMPLETED':
      return '可阅读'
    case 'RUNNING':
      return '导入中'
    case 'PENDING':
      return '排队中'
    default:
      return '导入失败'
  }
}

function BookCover({ src }: { src: string | null }) {
  const [failed, setFailed] = useState(false)
  const showImage = Boolean(src) && !failed
  return <div className={`ndr-book-cover${showImage ? '' : ' ndr-book-cover-placeholder'}`}
    data-testid="book-cover" aria-hidden="true" title={failed ? '封面加载失败' : undefined}>
    {showImage ? <img src={src!} alt="" width={72} height={108} loading="lazy" decoding="async"
      draggable={false} data-testid="book-cover-image" onError={() => setFailed(true)} />
      : <BookOpen size={24} strokeWidth={1.2} />}
  </div>
}

export function BookCard({ book, onDelete, deleting = false }: {
  book: BookOut
  onDelete?: (book: BookOut) => void
  deleting?: boolean
}) {
  const version = book.active_version
  const warnings = version?.warnings ?? []
  const coverUrl = version?.cover_resource_id
    ? `${resourceUrl(book.id, version.cover_resource_id)}?v=${encodeURIComponent(version.id)}`
    : null
  return (
    <Card asChild><article className="card ndr-book-card" data-testid="book-card">
      <CardHeader>
        <BookCover key={coverUrl ?? 'placeholder'} src={coverUrl} />
        <div className="ndr-book-heading">
          <h3>{book.title}</h3>
          <div className="ndr-book-badges">
            <Badge className={`ndr-badge ndr-badge-${book.format.toLowerCase()}`}>{book.format}</Badge>
            <Badge>{statusLabel(book)}</Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent>
      <p className="ndr-book-position">最后读到 · {book.last_read_chapter_title ?? '尚未记录'}</p>
      <details className="ndr-book-details"><summary>书籍信息</summary>
      <dl>
        <dt>正文长度</dt>
        <dd>{version ? `${version.canonical_length_cp} 字符` : '—'}</dd>
        <dt>编码</dt>
        <dd>
          {version ? (version.encoding === 'xml' ? 'XML（EPUB 文档自带）' : version.encoding) : '—'}
        </dd>
        <dt>最后读到</dt>
        <dd>{book.last_read_chapter_title ?? '尚未记录'}</dd>
        <dt>导入时间</dt>
        <dd>{new Date(book.created_at).toLocaleString('zh-CN')}</dd>
      </dl>
      </details>
      {warnings.length > 0 && (
        <details>
          <summary>导入警告（{warnings.length}）</summary>
          <ul>
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </details>
      )}
      </CardContent>
      <CardFooter className="ndr-book-actions">
        {deleting ? <p role="status">正在清理书籍记录，请等待完成。</p> : <Link className="ndr-button ndr-primary" to={`/books/${book.id}/read`}>
          开始阅读
          <ArrowUpRight size={15} aria-hidden="true" />
        </Link>}
        {onDelete && <Button
          type="button"
          className="ndr-danger"
          variant="danger"
          disabled={deleting}
          title={deleting ? '删除正在进行，请等待书籍清理完成。' : undefined}
          onClick={() => onDelete(book)}
          data-testid={`delete-book-${book.id}`}
        ><Trash2 size={14} aria-hidden="true" />{deleting ? '正在删除…' : '删除'}</Button>}
      </CardFooter>
    </article></Card>
  )
}
