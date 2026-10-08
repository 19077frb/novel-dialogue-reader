import { Children, cloneElement, isValidElement, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

/** Display-only paging: never change the collection used for selection or submission. */
export function useListPagination(count: number, pageSize = 20, scope = '') {
  const [selection, setSelection] = useState({ scope, page: 0 })
  const pageCount = Math.max(1, Math.ceil(count / pageSize))
  const page = selection.scope === scope ? Math.min(selection.page, pageCount - 1) : 0
  useEffect(() => {
    setSelection(previous => previous.scope === scope && previous.page === page
      ? previous : { scope, page })
  }, [scope, page])
  return { count, page, pageCount, pageSize,
    isVisible: (index: number) => index >= page * pageSize && index < (page + 1) * pageSize,
    setPage: (next: number) => setSelection({ scope, page: Math.max(0, next) }),
  }
}

type Pagination = ReturnType<typeof useListPagination>
type RemotePage = { hasMore?: boolean; loading?: boolean; loadMore?: () => Promise<unknown> }

export function ListPagination({ pagination: p, label, hasMore = false, loading = false, loadMore }: {
  pagination: Pagination; label: string;
} & RemotePage) {
  const [error, setError] = useState('')
  const [requesting, setRequesting] = useState(false)
  const busy = loading || requesting
  const last = p.page === p.pageCount - 1
  const next = async () => {
    if (busy) return
    setError('')
    if (!last) { p.setPage(p.page + 1); return }
    if (!hasMore || !loadMore) return
    setRequesting(true)
    // A partially filled last page must show newly appended rows before advancing.
    try { await loadMore(); p.setPage(Math.floor(p.count / p.pageSize)) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '读取失败，请重试。') }
    finally { setRequesting(false) }
  }
  if (p.count <= p.pageSize && !hasMore && !error) return null
  return <nav className="ndr-list-pagination" aria-label={`${label}分页`}>
    <span className="hint" aria-live="polite">第 {p.page + 1} 页 · {p.count ? p.page * p.pageSize + 1 : 0}–{Math.min((p.page + 1) * p.pageSize, p.count)} / {hasMore ? '已加载 ' : '共 '}{p.count} 项{hasMore ? '（还有更多）' : ''}</span>
    <button type="button" disabled={busy || p.page === 0} title={busy ? '正在读取列表，请稍候。' : p.page === 0 ? '已是第一页。' : undefined}
      onClick={() => { setError(''); p.setPage(p.page - 1) }}>上一页</button>
    <button type="button" disabled={busy || (last && !hasMore)} title={busy ? '正在读取列表，请稍候。' : last && !hasMore ? '已是最后一页。' : undefined}
      onClick={() => void next()}>{busy ? '正在读取…' : '下一页'}</button>
    {error && <p role="alert" className="status-error">下一页读取失败：{error}；请点击“下一页”重试。</p>}
  </nav>
}

/** Keep keyed forms and running task components mounted across pages. */
export function PaginatedItems({ children, label, pageSize = 20, scope = '', className, listTag = 'div', ...remote }: {
  children: ReactNode; label: string; pageSize?: number; scope?: string;
  className?: string; listTag?: 'div' | 'ul';
} & RemotePage) {
  const items = Children.toArray(children)
  const pagination = useListPagination(items.length, pageSize, scope)
  const Container = listTag
  return <>
    <Container className={className}>{items.map((child, index) => {
      const hidden = !pagination.isVisible(index)
      // Native list items retain valid HTML; custom editors need a hidden wrapper.
      return isValidElement<{ hidden?: boolean }>(child) && typeof child.type === 'string'
        ? cloneElement(child, { hidden })
        : <div key={isValidElement(child) ? child.key : index} hidden={hidden}>{child}</div>
    })}</Container>
    <ListPagination key={scope} pagination={pagination} label={label} {...remote} />
  </>
}
