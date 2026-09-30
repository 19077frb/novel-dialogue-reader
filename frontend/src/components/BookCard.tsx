import { Link } from 'react-router-dom'

import type { BookOut } from '../api/types'

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

export function BookCard({ book, onDelete, deleting = false }: {
  book: BookOut
  onDelete?: (book: BookOut) => void
  deleting?: boolean
}) {
  const version = book.active_version
  const warnings = version?.warnings ?? []
  return (
    <article className="card ndr-book-card" data-testid="book-card">
      <header>
        <h3>{book.title}</h3>
        <span className={`ndr-badge ndr-badge-${book.format.toLowerCase()}`}>{book.format}</span>
        <span className="ndr-badge">{statusLabel(book)}</span>
      </header>
      <dl>
        <dt>正文长度</dt>
        <dd>{version ? `${version.canonical_length_cp} 码点` : '—'}</dd>
        <dt>编码</dt>
        <dd>
          {version ? (version.encoding === 'xml' ? 'XML（EPUB 文档自带）' : version.encoding) : '—'}
        </dd>
        <dt>阅读位置</dt>
        <dd>{book.read_position_cp}</dd>
        <dt>导入时间</dt>
        <dd>{new Date(book.created_at).toLocaleString('zh-CN')}</dd>
      </dl>
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
      <Link className="ndr-primary-link" to={`/books/${book.id}/read`}>
        开始阅读
      </Link>
      {onDelete && <button
        type="button"
        className="ndr-danger"
        disabled={deleting}
        onClick={() => onDelete(book)}
        data-testid={`delete-book-${book.id}`}
      >{deleting ? '正在删除…' : '删除'}</button>}
    </article>
  )
}
