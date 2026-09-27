/** 导出进度与校验报告（T15B）：状态、内部检查逐项、标准检查状态。 */
import type { ExportArtifactOut } from '../api/types'

const STATE_LABEL: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '生成中',
  COMPLETED: '已完成',
  FAILED: '失败',
}

export interface ExportProgressProps {
  artifact: ExportArtifactOut | null
  busy: boolean
}

export function ExportProgress({ artifact, busy }: ExportProgressProps) {
  if (busy) {
    return (
      <p className="hint" role="status" aria-live="polite" data-testid="export-progress">
        正在生成…（本地打包，不调用模型）
      </p>
    )
  }
  if (!artifact) return null

  const internal = (artifact.validation?.internal ?? {}) as {
    ok?: boolean
    checks?: Record<string, boolean>
    missing_fragments?: string[]
    missing_resources?: string[]
  }
  const standard = (artifact.validation?.standard ?? {}) as {
    state?: string
    detail?: string
    version?: string
  }
  return (
    <div className="ndr-export-progress" data-testid="export-progress">
      <p role="status" aria-live="polite">
        状态：<strong data-testid="export-state">{STATE_LABEL[artifact.state] ?? artifact.state}</strong>
        {artifact.filename ? ` · ${artifact.filename}` : ''}
        {artifact.byte_size ? ` · ${Math.round(artifact.byte_size / 1024)} KB` : ''}
      </p>
      <ul className="hint" data-testid="export-checks">
        {Object.entries(internal.checks ?? {}).map(([name, ok]) => (
          <li key={name} data-testid={`export-check-${name}`}>
            {ok ? '✓' : '✗'} {name}
          </li>
        ))}
      </ul>
      <p className="hint" data-testid="export-standard">
        标准检查（EPUBCheck）：{standard.state ?? '未知'}
        {standard.detail ? ` — ${standard.detail}` : ''}
        {standard.version ? `（${standard.version}）` : ''}
      </p>
      {(internal.missing_fragments?.length ?? 0) > 0 && (
        <p className="status-error" data-testid="export-missing-fragments">
          导出缺少正文片段：{internal.missing_fragments?.slice(0, 3).join('、')}
        </p>
      )}
      {(internal.missing_resources?.length ?? 0) > 0 && (
        <p className="status-error" data-testid="export-missing-resources">
          导出缺少资源：{internal.missing_resources?.slice(0, 3).join('、')}
        </p>
      )}
      {artifact.state !== 'COMPLETED' && (
        <p className="status-error" data-testid="export-failed">
          导出未通过校验，文件不会提供下载。
        </p>
      )}
    </div>
  )
}