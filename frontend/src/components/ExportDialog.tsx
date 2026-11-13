/**
 * 导出对话框：范围 → 样式 → 初读策略 → 后端样张 → 生成 → 校验 → 下载。
 *
 * - 样张来自后端导出渲染器，放在**沙箱 iframe**（`sandbox=""`，无脚本）里展示，
 *   不是阅读页截图，也不执行任何脚本。
 * - 生成使用预览冻结的快照；若之后标注变化（新快照 hash 不同）会提示「快照已过期」。
 * - 全过程不调用模型；失败时展示校验原因，不提供下载。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useMemo, useRef, useState } from 'react'

import { createExport, exportKeys, fetchExportArtifact, previewExport } from '../api/exports'
import { fetchChapters, queryKeys } from '../api/books'
import type {
  ChapterOut,
  ExportArtifactOut,
  ExportFormat,
  ExportStylePreset,
  VisibilityPolicy,
} from '../api/types'
import { ExportDownload } from './ExportDownload'
import { ExportProgress } from './ExportProgress'
import { ExportScopePicker } from './ExportScopePicker'
import { ExportStylePreview } from './ExportStylePreview'
import { OperationTimer, useOperationClock } from './OperationTimer'
import { useGeneralSettings } from '../settings/preferences'

export interface ExportDialogProps {
  bookId: string
  open: boolean
  onClose: () => void
  /** 可由调用方传入章节（阅读页已有）；缺省时自行读取。 */
  chapters?: ChapterOut[]
  /** 书签位置，用于说明初读策略会用到它。 */
  readPositionCp?: number
}

interface ExportSubmission {
  bookId: string
  snapshotId: string
  snapshotHash: string
  format: ExportFormat
  style: ExportStylePreset
}

const STYLE_LABELS: Record<ExportStylePreset, string> = {
  color_and_label: '颜色 + 编号',
  color_only: '仅颜色',
  label_only: '仅编号',
}

export function ExportDialog({
  bookId,
  open,
  onClose,
  chapters,
  readPositionCp = 0,
}: ExportDialogProps) {
  const queryClient = useQueryClient()
  const exportClock = useOperationClock()
  const [scope, setScope] = useState<'book' | 'chapters'>('book')
  const [selectedChapterIds, setSelectedChapterIds] = useState<string[]>([])
  const [style, setStyle] = useState<ExportStylePreset>('color_and_label')
  const [settings] = useGeneralSettings()
  const [selectedPolicy, setVisibilityPolicy] = useState<VisibilityPolicy>('reread')
  const visibilityPolicy: VisibilityPolicy = settings.enableExperimentalFeatures ? selectedPolicy : 'reread'
  const [format, setFormat] = useState<ExportFormat>('epub')
  const [artifact, setArtifact] = useState<ExportArtifactOut | null>(null)
  // 与产物一起记录提交时的来源，后续预览或选项变化不能改写它。
  const [generatedFrom, setGeneratedFrom] = useState<ExportSubmission | null>(null)
  const [error, setError] = useState<string | null>(null)
  const artifactStatus = useQuery({
    queryKey: exportKeys.artifact(artifact?.id ?? ''),
    queryFn: ({ signal }) => fetchExportArtifact(artifact!.id, signal),
    enabled: open && Boolean(artifact && ['QUEUED', 'RUNNING'].includes(artifact.state)),
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state === 'COMPLETED' || state === 'FAILED' ? false : 1000
    },
  })

  useEffect(() => {
    const next = artifactStatus.data
    if (!next) return
    setArtifact((current) => current?.id === next.id && ['QUEUED', 'RUNNING'].includes(current.state)
      ? next : current)
  }, [artifactStatus.data])

  // 原生模态提供背景隔离及跨沙箱 iframe 的键盘焦点边界。
  const titleId = useId()
  const dialogRef = useRef<HTMLDialogElement | null>(null)

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = dialogRef.current
    dialog?.showModal()
    dialog?.focus()
    return () => {
      dialog?.close()
      if (previous?.isConnected) previous.focus()
    }
  }, [open])

  const chapterList = useQuery({
    queryKey: queryKeys.chapters(bookId),
    queryFn: ({ signal }) => fetchChapters(bookId, signal),
    enabled: open && !chapters,
  })
  const resolvedChapters = chapters ?? chapterList.data ?? []

  const chapterIds = scope === 'chapters' ? selectedChapterIds : null
  const preview = useQuery({
    queryKey: ['export-preview', bookId, scope, chapterIds, style, visibilityPolicy],
    queryFn: ({ signal }) =>
      previewExport(
        bookId,
        { chapterIds, style: { preset: style }, visibilityPolicy },
        signal,
      ),
    enabled: open && (scope === 'book' || selectedChapterIds.length > 0),
    // 每次打开都重新冻结：这样才能发现「标注在预览之后又变了」（快照过期提示）
    refetchOnMount: 'always',
    staleTime: 0,
  })

  useEffect(() => {
    if (!open) return
    // 保留上一次生成的产物（文件还在，仍可下载），只清错误提示
    setError(null)
    // 组件不会随关闭卸载，`refetchOnMount` 不生效：显式重新冻结，才能发现标注变化
    void preview.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const generation = useMutation({
    onMutate: exportClock.start,
    onSettled: exportClock.finish,
    mutationFn: ({ bookId, snapshotId, format, style }: ExportSubmission) =>
      createExport(bookId, {
        snapshotId,
        format,
        style: { preset: style },
        // 同一快照 + 格式 + 样式 → 后端复用已有产物；键只用于避免重复点击
        idempotencyKey: `export:${bookId}:${snapshotId}:${format}:${style}`,
      }),
    onSuccess: (data, submission) => {
      setArtifact((current) => current?.id === data.id && ['COMPLETED', 'FAILED'].includes(current.state)
        ? current : data)
      setGeneratedFrom(submission)
      setError(null)
      void queryClient.invalidateQueries({ queryKey: exportKeys.artifact(data.id) })
    },
    onError: (err: unknown) => setError(err instanceof Error ? err.message : '导出失败'),
  })

  const counts = preview.data?.counts ?? {}
  const warnings = preview.data?.warnings ?? []
  const staleSnapshot = useMemo(
    () =>
      Boolean(
        generatedFrom &&
          preview.data &&
          preview.data.snapshot_hash !== generatedFrom.snapshotHash,
      ),
    [generatedFrom, preview.data],
  )
  const changedOptions = generatedFrom &&
    (generatedFrom.format !== format || generatedFrom.style !== style)

  if (!open) return null

  return (
    <dialog
      className="ndr-dialog-backdrop"
      data-testid="export-dialog"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      aria-modal="true"
      aria-labelledby={titleId}
      ref={dialogRef}
      tabIndex={-1}
    >
      <div className="ndr-dialog">
        <header className="ndr-dialog-header">
          <h2 id={titleId}>导出</h2>
          <button type="button" onClick={onClose} data-testid="export-close">
            关闭
          </button>
        </header>
        <OperationTimer {...exportClock.clock} />

        <ExportScopePicker
          chapters={resolvedChapters}
          scope={scope}
          selectedChapterIds={selectedChapterIds}
          onScopeChange={setScope}
          onSelectionChange={setSelectedChapterIds}
        />
        <ExportStylePreview value={style} onChange={setStyle} />

        {settings.enableExperimentalFeatures && <fieldset className="ndr-export-policy" data-testid="export-policy">
          <legend>标注显示模式</legend>
          <label>
            <input
              type="radio"
              name="export-policy"
              checked={visibilityPolicy === 'position_safe'}
              onChange={() => setVisibilityPolicy('position_safe')}
              data-testid="export-policy-position-safe"
            />
            初读安全（试验，只到阅读位置 {readPositionCp} 为止的证据）
          </label>
          <label>
            <input
              type="radio"
              name="export-policy"
              checked={visibilityPolicy === 'reread'}
              onChange={() => setVisibilityPolicy('reread')}
              data-testid="export-policy-reread"
            />
            重读（显示完整标注）
          </label>
        </fieldset>}

        <fieldset className="ndr-export-format" data-testid="export-format">
          <legend>格式</legend>
          <div className="ndr-radio-row">
            <label>
              <input
                type="radio"
                name="export-format"
                checked={format === 'epub'}
                onChange={() => setFormat('epub')}
                data-testid="export-format-epub"
              />
              EPUB
            </label>
            <label>
              <input
                type="radio"
                name="export-format"
                checked={format === 'html'}
                onChange={() => setFormat('html')}
                data-testid="export-format-html"
              />
              单文件 HTML
            </label>
          </div>
        </fieldset>

        {preview.isPending && <p className="hint">正在冻结快照并生成样张…</p>}
        {preview.isError && <p className="status-error">样张生成失败。</p>}
        {preview.data && (
          <div className="ndr-export-preview" data-testid="export-preview">
            <p className="hint" data-testid="export-coverage">
              覆盖统计：总计 {counts.total ?? 0} · 已接受 {counts.accepted ?? 0} · 暂定{' '}
              {counts.provisional ?? 0} · 未知 {counts.unknown ?? 0} · 过期 {counts.stale ?? 0} ·
              未处理 {counts.unprocessed_quotes ?? 0}
              {counts.withheld ? ` · 初读遮断 ${counts.withheld}` : ''}
            </p>
            {warnings.length > 0 && (
              <ul className="hint" data-testid="export-warnings">
                {warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}
            <p className="hint">后端样张（沙箱容器，不执行脚本）：</p>
            <iframe
              title="导出样张"
              className="ndr-export-sample"
              sandbox=""
              srcDoc={preview.data.sample_html}
              data-testid="export-sample"
            />
            <button
              type="button"
              className="ndr-primary"
              disabled={generation.isPending}
              onClick={() => preview.data && generation.mutate({
                bookId,
                snapshotId: preview.data.snapshot_id,
                snapshotHash: preview.data.snapshot_hash,
                format,
                style,
              })}
              data-testid="export-generate"
              title={generation.isPending ? '正在生成导出文件，请等待生成完成。' : undefined}
            >
              {generation.isPending ? '正在生成…' : `生成 ${format.toUpperCase()}`}
            </button>
          </div>
        )}

        {error && (
          <p className="status-error" data-testid="export-error">
            {error}
          </p>
        )}
        {staleSnapshot && (
          <p className="hint" data-testid="export-stale-snapshot">
            导出内容或设置已经变化：下面是新快照的样张，已生成的文件仍使用旧快照。需要最新内容请重新生成。
          </p>
        )}
        {changedOptions && generatedFrom && (
          <p className="hint" data-testid="export-changed-options">
            已生成的文件使用 {generatedFrom.format.toUpperCase()}、{STYLE_LABELS[generatedFrom.style]}，
            与当前选择的格式或样式不同，仍可下载原文件。需要当前选择的文件请重新生成。
          </p>
        )}
        <ExportProgress artifact={artifact} busy={generation.isPending} />
        {artifactStatus.error && (
          <p className="status-error" role="alert">
            {artifactStatus.error instanceof Error ? artifactStatus.error.message : '读取导出状态失败'}
          </p>
        )}
        {artifact && <ExportDownload artifact={artifact} />}
      </div>
    </dialog>
  )
}
