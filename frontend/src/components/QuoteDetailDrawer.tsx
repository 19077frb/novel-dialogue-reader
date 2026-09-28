/**
 * 确认抽屉：普通对白与待确认项共用同一个抽屉。
 *
 * - 上下文/候选证据只读；「展开更多原文」不调用模型，「局部复核」是显式的付费操作。
 * - 更正/撤销/延后都不调用模型；提交旧版本会得到 409，这里提示冲突并刷新为最新状态。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'

import { ApiError } from '../api/client'
import { fetchQuoteDetail } from '../api/books'
import {
  deferReviewItem,
  fetchReviewItemDetail,
  flagReviewItem,
  reviewKeys,
  submitQuoteCorrection,
  submitGapCorrection,
  undoCorrection,
  type QuoteCorrectionInput,
} from '../api/review'
import type { GapDecision } from '../api/types'
import { CorrectionForm } from './CorrectionForm'
import { GapDecisionControls } from './GapDecisionControls'
import { QuoteContext } from './QuoteContext'
import { RecheckPanel } from './RecheckPanel'

const CONTEXT_STEPS = [120, 600, 2000]

export interface QuoteDetailDrawerProps {
  quoteId: string | null
  reviewItemId?: string | null
  onClose: () => void
  onCorrected?: () => void
}

export function QuoteDetailDrawer({
  quoteId,
  reviewItemId = null,
  onClose,
  onCorrected,
}: QuoteDetailDrawerProps) {
  const queryClient = useQueryClient()
  const [contextWindowCp, setContextWindowCp] = useState(CONTEXT_STEPS[0])
  const [notice, setNotice] = useState<string | null>(null)
  const [errorText, setErrorText] = useState<string | null>(null)
  const [lastCorrectionId, setLastCorrectionId] = useState<string | null>(null)
  const [showRecheck, setShowRecheck] = useState(false)

  useEffect(() => {
    setNotice(null)
    setErrorText(null)
    setLastCorrectionId(null)
    setShowRecheck(false)
    setContextWindowCp(CONTEXT_STEPS[0])
  }, [quoteId])

  // 可访问性：抽屉是一个有标题的对话框区域；打开时移入焦点，Escape 关闭并归还焦点。
  const titleId = useId()
  const drawerRef = useRef<HTMLElement | null>(null)
  const closeRef = useRef(onClose)
  closeRef.current = onClose

  useEffect(() => {
    if (!quoteId) return
    const previous = document.activeElement as HTMLElement | null
    drawerRef.current?.focus()
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
        previous.focus()
      }
    }
  }, [quoteId])

  const detail = useQuery({
    queryKey: reviewKeys.quoteDetail(quoteId ?? '', contextWindowCp),
    queryFn: ({ signal }) =>
      fetchQuoteDetail(quoteId as string, { contextWindowCp, signal }),
    enabled: Boolean(quoteId),
  })
  const itemDetail = useQuery({
    queryKey: reviewKeys.detail(reviewItemId ?? ''),
    queryFn: ({ signal }) => fetchReviewItemDetail(reviewItemId as string, signal),
    enabled: Boolean(reviewItemId),
  })

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['review-items'] })
    void queryClient.invalidateQueries({ queryKey: ['quote-detail'] })
    void queryClient.invalidateQueries({ queryKey: ['annotations'] })
    onCorrected?.()
  }

  const handleConflict = (err: unknown): boolean => {
    if (err instanceof ApiError && err.code === 'VERSION_CONFLICT') {
      setErrorText('这条对白已被其它操作更新：已为你刷新为最新版本，请重新确认。')
      void queryClient.invalidateQueries({ queryKey: ['quote-detail'] })
      void queryClient.invalidateQueries({ queryKey: ['review-items'] })
      return true
    }
    return false
  }

  const correction = useMutation({
    mutationFn: (input: QuoteCorrectionInput) =>
      submitQuoteCorrection(quoteId as string, input),
    onSuccess: (payload) => {
      setLastCorrectionId(payload.correction_id || (payload.correction_ids ?? [])[0] || null)
      setNotice(
        `已确认 ${(payload.affected_quote_ids ?? []).length} 条；另有 ${(payload.stale_quote_ids ?? []).length} 条下游需要重新确认。`,
      )
      setErrorText(null)
      refreshAll()
    },
    onError: (err: unknown) => {
      if (!handleConflict(err)) {
        setErrorText(err instanceof Error ? err.message : '更正失败')
      }
    },
  })

  const undo = useMutation({
    mutationFn: (correctionId: string) => undoCorrection(correctionId),
    onSuccess: (payload) => {
      setNotice(`已撤销更正，恢复了 ${(payload.affected_quote_ids ?? []).length} 条标注。`)
      setLastCorrectionId(null)
      refreshAll()
    },
    onError: (err: unknown) => {
      if (!handleConflict(err)) {
        setErrorText(err instanceof Error ? err.message : '撤销失败')
      }
    },
  })

  const defer = useMutation({
    mutationFn: () => deferReviewItem(reviewItemId as string, '稍后处理'),
    onSuccess: () => {
      setNotice('已跳过（延后）：它会留在待确认队列里，随时可以找回。')
      refreshAll()
    },
    onError: (err: unknown) => setErrorText(err instanceof Error ? err.message : '延后失败'),
  })

  const flag = useMutation({
    mutationFn: () => flagReviewItem(quoteId as string, { note: '用户主动标记' }),
    onSuccess: () => {
      setNotice('已加入待确认队列。')
      refreshAll()
    },
    onError: (err: unknown) => setErrorText(err instanceof Error ? err.message : '标记失败'),
  })

  const gapDecision = useMutation({
    mutationFn: (decision: GapDecision) =>
      submitGapCorrection(detail.data?.gap_before?.gap_id as string, { decision }),
    onSuccess: (payload) => {
      setNotice(
        `Gap 已确认（${payload.decision}）：影响 ${(payload.affected_quote_ids ?? []).length} 条引语。`,
      )
      refreshAll()
    },
    onError: (err: unknown) => {
      if (!handleConflict(err)) {
        setErrorText(err instanceof Error ? err.message : 'Gap 更正失败')
      }
    },
  })

  if (!quoteId) return null

  const data = detail.data
  const busy =
    correction.isPending || undo.isPending || defer.isPending || flag.isPending || gapDecision.isPending

  return (
    <aside
      className="ndr-drawer"
      role="dialog"
      aria-labelledby={titleId}
      ref={drawerRef}
      tabIndex={-1}
      data-testid="quote-detail-drawer"
    >
      <header className="ndr-drawer-header">
        <h2 id={titleId}>对白确认</h2>
        <button type="button" onClick={onClose} data-testid="drawer-close">
          关闭
        </button>
      </header>

      {detail.isPending && <p className="hint">正在读取详情…</p>}
      {detail.isError && <p className="status-error">详情读取失败。</p>}

      {data && (
        <>
          <p className="ndr-drawer-quote" data-testid="drawer-quote">
            {data.quote.delimited_text}
          </p>
          {itemDetail.data && (
            <p className="hint" data-testid="drawer-queue-item">
              队列：{itemDetail.data.item.reason} · {itemDetail.data.item.queue_status}
              （{(itemDetail.data.allowed_actions ?? []).join(' / ')}）
            </p>
          )}
          {notice && (
            <p className="hint" data-testid="drawer-notice">
              {notice}
            </p>
          )}

          <QuoteContext
            before={data.context_before}
            text={data.quote.delimited_text}
            after={data.context_after}
            contextWindowCp={contextWindowCp}
            onExpand={() => {
              const index = CONTEXT_STEPS.indexOf(contextWindowCp)
              setContextWindowCp(CONTEXT_STEPS[Math.min(index + 1, CONTEXT_STEPS.length - 1)])
            }}
          />

          {data.annotation && (
            <dl className="ndr-drawer-annotation" data-testid="drawer-annotation">
              <dt>状态</dt>
              <dd>{data.annotation.status}</dd>
              <dt>编号</dt>
              <dd>{data.annotation.label ?? '（没有编号）'}</dd>
              <dt>类型</dt>
              <dd>{data.annotation.kind}</dd>
              <dt>锁定</dt>
              <dd>{data.annotation.user_locked ? '已人工锁定' : '未锁定'}</dd>
              <dt>版本</dt>
              <dd data-testid="drawer-annotation-version">{data.annotation.version}</dd>
            </dl>
          )}

          <CorrectionForm
            annotation={data.annotation ?? null}
            sceneGroups={data.scene_groups ?? []}
            sceneVersion={data.scene?.version ?? null}
            busy={busy}
            errorText={errorText}
            onSubmit={(input) => correction.mutate(input)}
          />

          <div className="ndr-drawer-actions">
            <button
              type="button"
              disabled={busy || !lastCorrectionId}
              onClick={() => lastCorrectionId && undo.mutate(lastCorrectionId)}
              data-testid="drawer-undo"
              title={lastCorrectionId ? '撤销刚才的更正' : '先做一次更正再撤销'}
            >
              撤销刚才的更正
            </button>
            <button
              type="button"
              disabled={busy || Boolean(reviewItemId)}
              onClick={() => flag.mutate()}
              data-testid="drawer-flag"
            >
              标记为待确认
            </button>
            <button
              type="button"
              disabled={busy || !reviewItemId}
              onClick={() => defer.mutate()}
              data-testid="drawer-defer"
            >
              跳过（延后）
            </button>
          </div>

          {data.gap_before && (
            <GapDecisionControls
              gap={data.gap_before}
              busy={busy}
              onDecide={(decision) => gapDecision.mutate(decision)}
            />
          )}

          <details open={showRecheck} onToggle={(event) => setShowRecheck(event.currentTarget.open)}>
            <summary data-testid="recheck-toggle">局部复核（会调用模型，可能产生费用）</summary>
            {showRecheck && (
              <RecheckPanel quoteId={quoteId} onStarted={() => refreshAll()} />
            )}
          </details>
        </>
      )}
    </aside>
  )
}