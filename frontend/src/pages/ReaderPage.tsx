import { useMutation, useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { fetchBook, fetchChapters, fetchContent, queryKeys, saveReadingProgress } from '../api/books'
import { ApiError } from '../api/client'
import type { ChapterOut, ContentNodeOut, ReadingMode } from '../api/types'
import { ChapterNavigation } from '../components/ChapterNavigation'
import { DocumentRenderer } from '../components/DocumentRenderer'

/** 找到视口内第一个节点对应的起点；用于保存阅读位置（纯函数，便于测试）。 */
export function findCurrentStartCp(nodes: HTMLElement[]): number | null {
  if (nodes.length === 0) return null
  for (const node of nodes) {
    const top = node.getBoundingClientRect().top
    const raw = node.dataset.startCp
    if (top >= 0 && raw !== undefined) return Number(raw)
  }
  const last = nodes[nodes.length - 1]?.dataset.startCp
  return last !== undefined ? Number(last) : null
}

export default function ReaderPage() {
  const { bookId } = useParams<{ bookId: string }>()
  const documentRef = useRef<HTMLDivElement>(null)
  const lastSavedRef = useRef<number | null>(null)
  const [chapterId, setChapterId] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [pages, setPages] = useState<ContentNodeOut[][]>([])
  const [notice, setNotice] = useState<string | null>(null)

  const book = useQuery({
    queryKey: queryKeys.book(bookId ?? ''),
    queryFn: ({ signal }) => fetchBook(bookId as string, signal),
    enabled: Boolean(bookId),
  })

  const chapters = useQuery({
    queryKey: queryKeys.chapters(bookId ?? ''),
    queryFn: ({ signal }) => fetchChapters(bookId as string, signal),
    enabled: Boolean(bookId) && book.isSuccess,
  })

  const content = useQuery({
    queryKey: queryKeys.content(bookId ?? '', chapterId, cursor),
    queryFn: ({ signal }) =>
      fetchContent(bookId as string, { chapterId, cursor, limit: 500 }, signal),
    enabled: Boolean(bookId) && chapterId !== null,
  })

  // 默认章节：书签所在章节（没有书签就用第一章）。
  useEffect(() => {
    if (!book.data || !chapters.data || chapterId !== null) return
    const position = book.data.read_position_cp
    const match =
      chapters.data.find((chapter) => position >= chapter.start_cp && position < chapter.end_cp) ??
      chapters.data[0]
    setChapterId(match ? match.id : null)
  }, [book.data, chapters.data, chapterId])

  useEffect(() => {
    setPages([])
    setCursor(null)
    lastSavedRef.current = null
  }, [chapterId])

  useEffect(() => {
    if (!content.data) return
    setPages((previous) => (cursor ? [...previous, content.data.nodes] : [content.data.nodes]))
  }, [content.data, cursor])

  const nodes = useMemo(() => pages.flat(), [pages])
  const activeChapter = chapters.data?.find((chapter) => chapter.id === chapterId) ?? null

  const progress = useMutation({
    mutationFn: (input: {
      readPositionCp: number
      readingMode: ReadingMode
      expectedVersion: number
    }) =>
      saveReadingProgress(bookId as string, {
        bookVersionId: book.data?.active_version_id ?? '',
        readPositionCp: input.readPositionCp,
        readingMode: input.readingMode,
        expectedVersion: input.expectedVersion,
      }),
    onSuccess: () => setNotice(null),
    onError: async (error: unknown) => {
      if (error instanceof ApiError && error.code === 'VERSION_CONFLICT') {
        // 服务端版本更新了：刷新书籍后按最新版本重试一次，绝不覆盖较新的写入。
        const fresh = await book.refetch()
        if (fresh.data) {
          progress.mutate({
            readPositionCp: fresh.data.read_position_cp,
            readingMode: fresh.data.reading_mode,
            expectedVersion: fresh.data.version,
          })
        }
        return
      }
      setNotice('阅读位置保存失败，稍后会自动重试。')
    },
  })

  const persistPosition = useCallback(
    (readPositionCp: number) => {
      if (!book.data) return
      if (lastSavedRef.current === readPositionCp) return
      lastSavedRef.current = readPositionCp
      progress.mutate({
        readPositionCp,
        readingMode: book.data.reading_mode,
        expectedVersion: book.data.version,
      })
    },
    [book.data, progress],
  )

  const handleScroll = useCallback(() => {
    const container = documentRef.current
    if (!container) return
    const elements = Array.from(
      container.querySelectorAll<HTMLElement>('[data-node-id]'),
    ) as HTMLElement[]
    const startCp = findCurrentStartCp(elements)
    if (startCp !== null) persistPosition(startCp)
  }, [persistPosition])

  const handleChapterSelect = useCallback(
    (chapter: ChapterOut) => {
      setChapterId(chapter.id)
      setNotice(null)
      persistPosition(chapter.start_cp)
    },
    [persistPosition],
  )

  if (!bookId) return <p className="status-error">缺少书籍 ID。</p>

  const encodingLabel = book.data?.active_version
    ? book.data.active_version.encoding === 'xml'
      ? 'EPUB'
      : book.data.active_version.encoding
    : ''

  return (
    <div className="ndr-page ndr-reader">
      <header className="ndr-reader-header card">
        <div>
          <h2>{book.data?.title ?? '载入中…'}</h2>
          <p className="hint">
            {book.data?.format ?? ''}
            {encodingLabel ? ` · ${encodingLabel}` : ''}
            {book.data ? ` · 书签位置 ${book.data.read_position_cp}` : ''}
          </p>
        </div>
        <Link to="/library">返回书架</Link>
      </header>

      {notice && (
        <p className="hint" data-testid="reader-notice">
          {notice}
        </p>
      )}
      {book.isError && <p className="status-error">书籍读取失败。</p>}

      <div className="ndr-reader-body">
        <aside className="card ndr-reader-sidebar">
          {chapters.isSuccess && (
            <ChapterNavigation
              chapters={chapters.data}
              activeChapterId={chapterId}
              onSelect={handleChapterSelect}
            />
          )}
          {chapters.isPending && <p className="hint">正在读取目录…</p>}
        </aside>

        <section className="card ndr-reader-content" ref={documentRef} onScroll={handleScroll}>
          {chapterId === null && <p className="hint">这本书没有可显示的章节。</p>}
          {content.isPending && chapterId !== null && <p className="hint">正在读取正文…</p>}
          {content.isError && <p className="status-error">正文读取失败。</p>}
          {nodes.length > 0 && (
            <>
              <h1 className="ndr-chapter-heading">{activeChapter?.title ?? '正文'}</h1>
              <DocumentRenderer bookId={bookId} nodes={nodes} />
            </>
          )}
          {content.data?.next_cursor && (
            <button
              type="button"
              onClick={() => setCursor(content.data?.next_cursor ?? null)}
              data-testid="load-more"
            >
              加载本章后续内容
            </button>
          )}
        </section>
      </div>
    </div>
  )
}