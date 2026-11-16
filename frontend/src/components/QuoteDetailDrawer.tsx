import { TriangleAlert } from 'lucide-react'
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
import { annotationSpeakerName, annotationStatusLabel, quoteKindLabel, reviewReasonLabel, queueStatusLabel, correctionActionLabel, gapDecisionLabel } from '../ui/labels'
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

  // 原生模态隔离背景交互和键盘焦点；关闭仍只隐藏详情，不取消在途保存或复核。
  const titleId = useId()
  const drawerRef = useRef<HTMLDialogElement | null>(null)
  const backdropPointer = useRef<number | null>(null)

  useEffect(() => {
    if (!quoteId) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = drawerRef.current
    const scrollRoots = [document.documentElement, document.body]
    const previousOverflow = scrollRoots.map((element) => ({
      value: element.style.getPropertyValue('overflow'),
      priority: element.style.getPropertyPriority('overflow'),
    }))
    dialog?.showModal()
    dialog?.focus({ preventScroll: true })
    scrollRoots.forEach((element) => element.style.setProperty('overflow', 'hidden'))
    return () => {
      backdropPointer.current = null
      dialog?.close()
      scrollRoots.forEach((element, index) => {
        const { value, priority } = previousOverflow[index]
        if (value) element.style.setProperty('overflow', value, priority)
        else element.style.removeProperty('overflow')
      })
      if (previous && typeof previous.focus === 'function' && document.contains(previous)) {
        previous.focus({ preventScroll: true })
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
        `场景间隔已确认（${gapDecisionLabel(payload.decision)}）：影响 ${(payload.affected_quote_ids ?? []).length} 条引语。`,
      )
      refreshAll()
    },
    onError: (err: unknown) => {
      if (!handleConflict(err)) {
        setErrorText(err instanceof Error ? err.message : '场景间隔更正失败')
      }
    },
  })

  if (!quoteId) return null

  const data = detail.data
  const busy =
    correction.isPending || undo.isPending || defer.isPending || flag.isPending || gapDecision.isPending

  return (
    <dialog
      className="ndr-drawer-backdrop"
      aria-modal="true"
      aria-labelledby={titleId}
      ref={drawerRef}
      tabIndex={-1}
      data-testid="quote-detail-drawer"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Tab') return
        // Chromium can hand focus to browser chrome at a native dialog's ends.
        // Keep both tab directions within the visible controls of this panel.
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
          'button, a[href], input, select, textarea, summary, [tabindex]',
        )).filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length > 0)
        const first = controls[0]
        const last = controls[controls.length - 1]
        const active = document.activeElement
        if (!first) {
          event.preventDefault()
          event.currentTarget.focus({ preventScroll: true })
        } else if (event.shiftKey && (active === first || active === event.currentTarget)) {
          event.preventDefault()
          last.focus()
        } else if (!event.shiftKey && (active === last || active === event.currentTarget)) {
          event.preventDefault()
          first.focus()
        }
      }}
      onPointerDown={(event) => {
        backdropPointer.current = event.target === event.currentTarget && event.button === 0
          ? event.pointerId : null
      }}
      onPointerCancel={() => { backdropPointer.current = null }}
      onPointerUp={(event) => {
        if (event.target !== event.currentTarget || backdropPointer.current !== event.pointerId) {
          backdropPointer.current = null
        }
      }}
      onClick={(event) => {
        const fromBackdrop = backdropPointer.current !== null
        backdropPointer.current = null
        if (fromBackdrop && event.target === event.currentTarget) onClose()
      }}
    >
      <div className="ndr-drawer">
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
              队列：{reviewReasonLabel(itemDetail.data.item.reason)} · {queueStatusLabel(itemDetail.data.item.queue_status)}
              （{(itemDetail.data.allowed_actions ?? []).map(correctionActionLabel).join(' / ')}）
            </p>
          )}
          {notice && (
            <p className="hint" data-testid="drawer-notice">
              {notice}
            </p>
          )}
          {itemDetail.data?.item.reason === 'MODEL_OUTPUT_WARNING' && (
            <p className="status-warning" role="status"><TriangleAlert size="1em" style={{ display: 'inline-block', verticalAlign: 'middle' }} aria-hidden="true" /> 模型结果未通过逐句校验，显示的人物仅是候选，请核对后确认。</p>
          )}
          {typeof itemDetail.data?.item.candidates?.warning === 'string' && (
            <p className="hint">{itemDetail.data.item.candidates.warning}</p>
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
              <dd title={data.annotation.status}>{annotationStatusLabel(data.annotation.status)}</dd>
              <dt>人物归属</dt>
              <dd>{annotationSpeakerName(data.annotation, data.scene_groups ?? [])}</dd>
              <dt>类型</dt>
              <dd title={data.annotation.kind}>{quoteKindLabel(data.annotation.kind)}</dd>
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
              title={busy ? '正在保存当前操作，请等待完成后再撤销。' : lastCorrectionId ? '撤销刚才的更正' : '先做一次更正再撤销'}
            >
              撤销刚才的更正
            </button>
            <button
              type="button"
              disabled={busy || Boolean(reviewItemId)}
              onClick={() => flag.mutate()}
              data-testid="drawer-flag"
              title={busy ? '正在保存当前操作，请等待完成。' : reviewItemId ? '这句对白已在待确认队列中。' : undefined}
            >
              标记为待确认
            </button>
            <button
              type="button"
              disabled={busy || !reviewItemId}
              onClick={() => defer.mutate()}
              data-testid="drawer-defer"
              title={busy ? '正在保存当前操作，请等待完成。' : !reviewItemId ? '这句对白尚未加入待确认队列，请先标记为待确认。' : undefined}
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
      </div>
    </dialog>
  )
}
