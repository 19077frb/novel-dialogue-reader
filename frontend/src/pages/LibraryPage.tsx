import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'

import { deleteBook, fetchBooks, importBook, queryKeys } from '../api/books'
import { ApiError } from '../api/client'
import type { BookOut, ImportResult } from '../api/types'
import { BookCard } from '../components/BookCard'
import { ImportDropzone } from '../components/ImportDropzone'
import { JobPanel } from '../components/JobPanel'
import { clearBatchProgress, isBatchRunning } from '../components/BatchProcessor'
import { OperationTimer, useOperationClock } from '../components/OperationTimer'
import { ReadErrorNotice } from '../components/ReadErrorNotice'

const ENCODING_OPTIONS = [
  { value: '', label: '自动检测（推荐）' },
  { value: 'utf-8', label: 'UTF-8' },
  { value: 'gb18030', label: 'GB18030 / GBK' },
  { value: 'big5', label: 'Big5' },
]

interface EncodingCandidate {
  encoding: string
  ok: boolean
  plausibility: number
  detail?: string
}

function candidatesOf(error: ApiError | null): EncodingCandidate[] {
  const raw = error?.details?.candidates
  if (!Array.isArray(raw)) return []
  return raw as EncodingCandidate[]
}

export default function LibraryPage() {
  const queryClient = useQueryClient()
  const importClock = useOperationClock()
  const deleteClock = useOperationClock()
  const [encoding, setEncoding] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null)

  const books = useQuery({
    queryKey: queryKeys.books(),
    queryFn: ({ signal }) => fetchBooks(signal),
  })

  const importMutation = useMutation({
    onMutate: importClock.start,
    onSettled: importClock.finish,
    mutationFn: (vars: { file: File; encoding: string }) =>
      importBook({
        file: vars.file,
        encoding: vars.encoding || undefined,
        title: title.trim() || undefined,
      }),
    onSuccess: (data) => {
      setResult(data)
      setError(null)
      setFile(null)
      void queryClient.invalidateQueries({ queryKey: queryKeys.books() })
    },
    onError: (err) => {
      setResult(null)
      setError(
        err instanceof ApiError
          ? err
          : new ApiError(0, {
              code: 'UNKNOWN',
              message: err instanceof Error ? err.message : '导入失败',
            }),
      )
    },
  })

  const deleteMutation = useMutation({
    onMutate: deleteClock.start,
    onSettled: deleteClock.finish,
    mutationFn: (book: BookOut) => deleteBook(book.id),
    onSuccess: (_data, book) => {
      clearBatchProgress(book.id)
      if (result?.book_id === book.id) setResult(null)
      queryClient.removeQueries({ predicate: (query) => query.queryKey.includes(book.id) })
      void queryClient.invalidateQueries({ queryKey: queryKeys.books() })
      setDeleteError(null)
      setDeleteNotice(`已删除《${book.title}》；书籍文件已移入数据目录回收区，应用内无法撤销。`)
    },
    onError: (reason) => {
      setDeleteError(reason instanceof Error ? reason.message : '删除失败')
    },
  })

  const handleDelete = (book: BookOut) => {
    setDeleteError(null)
    setDeleteNotice(null)
    if (isBatchRunning(book.id)) {
      setDeleteError('本书仍在批量处理，请先停止批量处理并等待结束后再删除。')
      return
    }
    if (!window.confirm(
      `确定删除《${book.title}》吗？\n导入时间：${new Date(book.created_at).toLocaleString('zh-CN')}\n` +
      `书籍 ID：${book.id}\n\n将删除本书的阅读进度、人物、标注和任务记录，应用内无法撤销。` +
      '书籍及导出文件移入数据目录回收区。同名的其他书籍不会受影响。',
    )) return
    deleteMutation.mutate(book)
  }

  const preview = typeof error?.details?.preview === 'string' ? error.details.preview : ''
  const candidates = candidatesOf(error)
  const warnings = result?.warnings ?? []

  return (
    <div className="ndr-page ndr-library">
      <header className="card ndr-page-header">
        <div>
          <h2>书架</h2>
          <p className="hint">
            导入 TXT/EPUB 书籍后即可阅读原文；识别说话人后按人物着色，可随时导出。
          </p>
        </div>
      </header>
      <section className="card">
        <h2>导入书籍</h2>
        <p className="hint">
          支持 TXT 与 EPUB。TXT 需要选对编码；不确定就保持“自动检测”，解析失败时会给出可用的编码候选与预演，
          不会静默丢字。
        </p>
        <ImportDropzone onFile={setFile} disabled={importMutation.isPending} />

        <div className="ndr-import-controls">
          <label>
            书名（可选）
            <input
              type="text"
              value={title}
              placeholder="留空则用文件内的书名或文件名"
              onChange={(event) => setTitle(event.target.value)}
              data-testid="import-title"
            />
          </label>
          <label>
            TXT 编码
            <select
              value={encoding}
              onChange={(event) => setEncoding(event.target.value)}
              data-testid="import-encoding"
            >
              {ENCODING_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="ndr-primary"
            disabled={!file || importMutation.isPending}
            onClick={() => file && importMutation.mutate({ file, encoding })}
            data-testid="import-submit"
          >
            {importMutation.isPending ? '正在导入…' : '开始导入'}
          </button>
        </div>

        {file && <p className="hint">已选择：{file.name}</p>}
        {!file && !importMutation.isPending && <p className="hint">先选择 TXT 或 EPUB 文件，再开始导入。</p>}
        <OperationTimer {...importClock.clock} />

        {importMutation.isPending && (
          <p data-testid="import-pending" className="hint">
            正在解析文件（EPUB 会校验包结构与体积，可能需要几秒）…
          </p>
        )}

        {result && (
          <div className="ndr-import-result" data-testid="import-result">
            <p className="status-ok">
              导入完成：{result.format}，{result.chapter_count} 章，{result.node_count} 个节点，
              {result.canonical_length_cp} 字符
              {result.encoding ? `，编码 ${result.encoding}` : '（EPUB 文档自带编码）'}
              {result.reused_version ? '（复用已有版本）' : ''}
            </p>
            <p className="hint">{result.reused_version ? '已复用原来的预处理结果，不覆盖你的调整。' : `自动预处理完成：章节修复 ${result.chapter_repairs_applied ?? 0} 处，引号修复 ${result.quote_repairs_applied ?? 0} 处。`}</p>
            <Link className="ndr-button" to={`/books/${result.book_id}/preprocessing`}>检查预处理结果</Link>
            {warnings.length > 0 && (
              <ul className="hint">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
            <JobPanel jobId={result.job_id} />
          </div>
        )}

        {error && (
          <div className="ndr-import-error" data-testid="import-error">
            <p className="status-error">
              导入失败（{error.code}）：{error.message}
            </p>
            {candidates.length > 0 && (
              <>
                <p className="hint">可用的编码候选（按可信度）：</p>
                <ul className="ndr-candidates">
                  {candidates.map((candidate) => (
                    <li key={candidate.encoding}>
                      <code>{candidate.encoding}</code>
                      <span>
                        {' '}
                        {candidate.ok ? `可用（可读性 ${candidate.plausibility}）` : '不可用'}
                      </span>
                      {candidate.ok && file && (
                        <button
                          type="button"
                          onClick={() =>
                            importMutation.mutate({ file, encoding: candidate.encoding })
                          }
                          data-testid={`retry-${candidate.encoding}`}
                        >
                          用 {candidate.encoding} 重试
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
            {preview && (
              <details open>
                <summary>有损预演（只用于挑编码，不是正文）</summary>
                <pre data-testid="import-preview">{preview}</pre>
              </details>
            )}
          </div>
        )}
      </section>

      <section className="card">
        <h2>全部书籍</h2>
        {deleteError && <p className="status-error" role="alert" data-testid="delete-book-error">{deleteError}</p>}
        {deleteNotice && <p className="status-ok" role="status">{deleteNotice}</p>}
        {books.isPending && <p className="hint">正在读取书架…</p>}
        {books.isError && <ReadErrorNotice label="书架读取失败" error={books.error} retrying={books.isFetching} onRetry={() => void books.refetch()} />}
        <OperationTimer {...deleteClock.clock} />
        {books.isSuccess && books.data.items.length === 0 && (
          <p className="hint" data-testid="library-empty">
            还没有书。导入 TXT 或 EPUB 后即可阅读；不需要填写任何 API 配置。
          </p>
        )}
        {books.isSuccess && books.data.items.length > 0 && (
          <div className="ndr-book-grid">
            {books.data.items.map((book) => (
              <BookCard key={book.id} book={book} onDelete={handleDelete}
                deleting={deleteMutation.isPending && deleteMutation.variables?.id === book.id} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
