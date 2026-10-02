import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  fetchQuoteNormalizations,
  queryKeys,
  refreshQuoteNormalizations,
  updateQuoteNormalization,
} from '../api/books'
import type { QuoteNormalizationOut } from '../api/types'
import { OperationTimer, useRequestClock } from './OperationTimer'
import { ReadErrorNotice } from './ReadErrorNotice'

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

export function moveClosingPoint(text: string, offset: number, direction: -1 | 1): number {
  const chars = Array.from(text)
  const stops = chars.flatMap((char, index) => /[。！？!?，,；;：:、…]/u.test(char) ? [index + 1] : [])
  return direction > 0 ? stops.find(point => point > offset) ?? chars.length : stops.reverse().find(point => point < offset) ?? 1
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

  const clock = useRequestClock(refreshMutation.isPending || updateMutation.isPending)
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
          title={refreshMutation.isPending ? '正在重新扫描，请等待完成后再刷新。' : undefined}
          data-testid="quote-normalization-refresh"
        >
          {refreshMutation.isPending ? '正在检测…' : '重新检测'}
        </button>
      </div>
      <OperationTimer {...clock} />

      {normalizations.isPending && <p className="hint">正在读取引号修复建议…</p>}
      {normalizations.isError && (
        <ReadErrorNotice label="引号修复建议读取失败" error={normalizations.error}
          retrying={normalizations.isFetching} onRetry={() => void normalizations.refetch()} />
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
            const chars = Array.from(item.original_text)
            const offset = Math.max(1, Math.min(chars.length, closeCp - item.opening_cp))
            const move = (point: number) => update({ close_cp: String(item.opening_cp + point) })
            const valid =
              Number.isInteger(closeCp) &&
              closeCp > item.opening_cp &&
              closeCp <= item.opening_cp + chars.length
            const dirty =
              draft.close_cp !== String(item.close_cp) ||
              draft.replacement !== item.replacement ||
              draft.status !== item.status

            return (
              <details className="ndr-normalization-card" key={item.id}>
                <summary>{item.status === 'ACTIVE' ? '已启用' : '已停用'} · {item.original_text.slice(0, 80)}{item.original_text.length > 80 ? '…' : ''}</summary>
                <div className="ndr-normalization-summary">
                  <code>{item.normalized_text}</code>
                  <small>
                    开引号 {item.opening_cp} · 虚拟闭合 {item.close_cp} · {item.reason}
                  </small>
                </div>
                <label className="ndr-field">
                  原文定位（点击或用左右键移动光标，闭合插在光标处）
                  <textarea readOnly rows={3} value={item.original_text} data-testid={`quote-normalization-text-${item.id}`} onSelect={event => {
                    const input = event.currentTarget
                    move(Math.max(1, Array.from(input.value.slice(0, input.selectionEnd)).length))
                  }} />
                </label>
                <div className="ndr-form-actions">
                  <button title={offset <= 1 ? '已到可用位置的最前端，不能再前移。' : undefined} disabled={offset <= 1} onClick={() => move(offset - 1)}>前移一字</button>
                  <button title={offset >= chars.length ? '已到段尾，不能再后移。' : undefined} disabled={offset >= chars.length} onClick={() => move(offset + 1)}>后移一字</button>
                  <button title={offset <= 1 ? '已到最前端，没有更早的可用标点位置。' : undefined} disabled={offset <= 1} onClick={() => move(moveClosingPoint(item.original_text, offset, -1))}>上一个标点后</button>
                  <button title={offset >= chars.length ? '已到段尾，没有后续标点位置。' : undefined} disabled={offset >= chars.length} onClick={() => move(moveClosingPoint(item.original_text, offset, 1))}>下一个标点后</button>
                  <button title={offset === chars.length ? '当前已在段尾，无需再移动。' : undefined} disabled={offset === chars.length} onClick={() => move(chars.length)}>移到段尾</button>
                </div>
                <p className="hint">预览只改变虚拟闭合位置；确认后点击“保存并重扫”。</p>
                <code data-testid={`quote-normalization-preview-${item.id}`}>{chars.slice(0, offset).join('')}<mark className="ndr-context-target">{draft.replacement}</mark>{chars.slice(offset).join('')}</code>
                <div className="ndr-normalization-edit">
                  <label>
                    闭合字符位置（从 0 开始）
                    <input
                      type="number"
                      min={item.opening_cp + 1}
                      max={item.opening_cp + chars.length}
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
                    className="ndr-primary"
                    disabled={!dirty || !valid || updateMutation.isPending}
                    title={updateMutation.isPending ? '正在保存并重新扫描，请等待完成。' : !dirty ? '请先修改闭合位置、字符或状态。' : !valid ? '请先将闭合位置调整到当前段落内。' : undefined}
                    onClick={() => updateMutation.mutate({ id: item.id, draft })}
                  >
                    保存并重扫
                  </button>
                  {!dirty && <p className="hint">位置或状态尚未修改。</p>}
                  {!valid && <p className="status-error">闭合位置必须位于当前原段落内。</p>}
                </div>
              </details>
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
