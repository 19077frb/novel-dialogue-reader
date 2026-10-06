/** 导出进度与校验报告：状态、内部检查逐项、标准检查状态。 */
import type { ExportArtifactOut } from '../api/types'

const STATE_LABEL: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '生成中',
  COMPLETED: '已完成',
  FAILED: '失败',
}
const CHECK_LABEL: Record<string, string> = {
  non_empty: '文件包含内容', readable_zip: '压缩包可读取',
  mimetype_first: '格式声明位于首项', mimetype_stored: '格式声明未压缩',
  mimetype_value: '格式声明正确', has_container: '存在书籍入口',
  has_opf: '存在书籍信息', has_nav: '存在目录', resource_closure: '图片等资源完整',
  no_external_references: '无需外部资源', no_localhost: '不依赖本机服务',
  text_consistency: '正文完整一致',
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
  const ledger = (artifact.validation?.identity_ledger ?? {}) as { omitted?: number }
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
            {ok ? '✓' : '✗'} {CHECK_LABEL[name] ?? name}
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
      {typeof ledger.omitted === 'number' && Number.isInteger(ledger.omitted) && ledger.omitted > 0 && (
        <p className="hint" data-testid="export-identity-omitted">
          有 {ledger.omitted} 位人物的完整资料需要未导出的章节、无法核验的位置或超过可携带限制，未写入文件。
          正文和已导出的对白标注不受影响；若需迁移完整人物资料，请导出整本。
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
