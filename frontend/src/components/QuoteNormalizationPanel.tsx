import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchQuoteNormalizations,
  queryKeys,
  refreshQuoteNormalizations,
  updateQuoteNormalization,
} from '../api/books'
import type { QuoteNormalizationOut } from '../api/types'

interface QuoteNormalizationPanelProps {
  bookId: string
}

interface DraftState {
  close_cp: string
  replacement: string
  status: QuoteNormalizationOut['status']
}

function draftKey(item: QuoteNormalizationOut): string {
  return `${item.id}:${item.version}:${item.close_cp}:${item.replacement}:${item.status}`
}

export function QuoteNormalizationPanel({ bookId }: QuoteNormalizationPanelProps) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Record<string, DraftState>>({})
  const [error, setError] = useState<string | null>(null)

  const normalizations = useQuery({
    queryKey: queryKeys.quoteNormalizations(bookId),
    queryFn: ({ signal }) => fetchQuoteNormalizations(bookId, signal),
  })
  const items = normalizations.data ?? []
  const activeItems = items.filter((item) => item.status === 'ACTIVE')

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.quoteNormalizations(bookId) })
    void queryClient.invalidateQueries({ queryKey: ['quotes', bookId] })
    void queryClient.invalidateQueries({ queryKey: ['annotations', bookId] })
  }

  const refreshMutation = useMutation({
    mutationFn: () => refreshQuoteNormalizations(bookId),
    onSuccess: () => {
      setError(null)
      setDrafts({})
      invalidate()
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '重新检测失败'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, draft }: { id: string; draft: DraftState }) =>
      updateQuoteNormalization(bookId, id, {
        close_cp: Number(draft.close_cp),
        replacement: draft.replacement,
        status: draft.status,
        expected_version: items.find((item) => item.id === id)?.version ?? 1,
      }),
    onSuccess: () => {
      setError(null)
      invalidate()
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '保存失败'),
  })

  return (
    <section className="card ndr-step-card" data-testid="quote-normalization-panel">
      <div className="ndr-step-heading">
        <div>
          <h3>引号修复</h3>
          <p className="hint">
            导入时会检查跨段未闭合的 <code>“</code>。确认结果只更新候选边界；
            不改写原文，也不会清除对白归属或人工锁定结果。
          </p>
        </div>
        <button
          type="button"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          data-testid="quote-normalization-refresh"
        >
          {refreshMutation.isPending ? '正在检测…' : '重新检测'}
        </button>
      </div>

      {normalizations.isPending && <p className="hint">正在读取引号修复建议…</p>}
      {normalizations.isError && (
        <p className="status-error">
          引号修复建议读取失败：
          {normalizations.error instanceof Error ? normalizations.error.message : '未知错误'}
        </p>
      )}
      {error && <p className="status-error" role="alert">{error}</p>}
      {normalizations.isSuccess && activeItems.length > 0 && (
        <p className="status-warning" data-testid="quote-normalization-warning">
          已启用 {activeItems.length} 条虚拟闭合；处理前请检查它们是否符合原文语义。
        </p>
      )}

      {items.length > 0 && (
        <div className="ndr-normalization-list">
          {items.map((item) => {
            const key = draftKey(item)
            const draft = drafts[key] ?? {
              close_cp: String(item.close_cp),
              replacement: item.replacement,
              status: item.status,
            }
            const update = (patch: Partial<DraftState>) => setDrafts((current) => ({
              ...current,
              [key]: { ...draft, ...patch },
            }))
            const closeCp = Number(draft.close_cp)
            const valid =
              Number.isInteger(closeCp) &&
              closeCp > item.opening_cp &&
              closeCp <= item.opening_cp + item.original_text.length
            const dirty =
              draft.close_cp !== String(item.close_cp) ||
              draft.replacement !== item.replacement ||
              draft.status !== item.status

            return (
              <article className="ndr-normalization-card" key={item.id}>
                <div className="ndr-normalization-summary">
                  <code>{item.normalized_text}</code>
                  <small>
                    开引号 {item.opening_cp} · 虚拟闭合 {item.close_cp} · {item.reason}
                  </small>
                </div>
                <div className="ndr-normalization-edit">
                  <label>
                    闭合码点（原段落内）
                    <input
                      type="number"
                      min={item.opening_cp + 1}
                      max={item.opening_cp + item.original_text.length}
                      value={draft.close_cp}
                      onChange={(event) => update({ close_cp: event.target.value })}
                      data-testid={`quote-normalization-close-${item.id}`}
                    />
                  </label>
                  <label>
                    插入字符
                    <input
                      value={draft.replacement}
                      onChange={(event) => update({ replacement: event.target.value })}
                      data-testid={`quote-normalization-replacement-${item.id}`}
                    />
                  </label>
                  <label>
                    状态
                    <select
                      value={draft.status}
                      onChange={(event) =>
                        update({ status: event.target.value as QuoteNormalizationOut['status'] })
                      }
                      data-testid={`quote-normalization-status-${item.id}`}
                    >
                      <option value="ACTIVE">启用</option>
                      <option value="DISABLED">停用</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={!dirty || !valid || updateMutation.isPending}
                    onClick={() => updateMutation.mutate({ id: item.id, draft })}
                  >
                    保存并重扫
                  </button>
                </div>
              </article>
            )
          })}
        </div>
      )}
      {normalizations.isSuccess && items.length === 0 && (
        <p className="hint">没有需要修复的跨段未闭合引号。</p>
      )}
    </section>
  )
}
