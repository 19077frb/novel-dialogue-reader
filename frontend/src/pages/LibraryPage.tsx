import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchBooks, importBook, queryKeys } from '../api/books'
import { ApiError } from '../api/client'
import type { ImportResult } from '../api/types'
import { BookCard } from '../components/BookCard'
import { ImportDropzone } from '../components/ImportDropzone'
import { JobPanel } from '../components/JobPanel'

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
  const [encoding, setEncoding] = useState('')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<ImportResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)

  const books = useQuery({
    queryKey: queryKeys.books(),
    queryFn: ({ signal }) => fetchBooks(signal),
  })

  const importMutation = useMutation({
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

  const preview = typeof error?.details?.preview === 'string' ? error.details.preview : ''
  const candidates = candidatesOf(error)
  const warnings = result?.warnings ?? []

  return (
    <div className="ndr-page ndr-library">
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

        {importMutation.isPending && (
          <p data-testid="import-pending" className="hint">
            正在解析文件（EPUB 会校验包结构与体积，可能需要几秒）…
          </p>
        )}

        {result && (
          <div className="ndr-import-result" data-testid="import-result">
            <p className="status-ok">
              导入完成：{result.format}，{result.chapter_count} 章，{result.node_count} 个节点，
              {result.canonical_length_cp} 码点
              {result.encoding ? `，编码 ${result.encoding}` : '（EPUB 文档自带编码）'}
              {result.reused_version ? '（复用已有版本）' : ''}
            </p>
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
        <h2>书架</h2>
        {books.isPending && <p className="hint">正在读取书架…</p>}
        {books.isError && <p className="status-error">书架读取失败，请确认后端已启动。</p>}
        {books.isSuccess && books.data.items.length === 0 && (
          <p className="hint" data-testid="library-empty">
            还没有书。导入 TXT 或 EPUB 后即可阅读；不需要填写任何 API 配置。
          </p>
        )}
        {books.isSuccess && books.data.items.length > 0 && (
          <div className="ndr-book-grid">
            {books.data.items.map((book) => (
              <BookCard key={book.id} book={book} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}