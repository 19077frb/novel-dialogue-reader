/** 导出下载：受控端点 + 文件名/哈希；重复下载不会重新生成。 */
import { exportDownloadUrl } from '../api/exports'
import type { ExportArtifactOut } from '../api/types'

export interface ExportDownloadProps {
  artifact: ExportArtifactOut
}

export function ExportDownload({ artifact }: ExportDownloadProps) {
  if (!artifact.download_available) {
    return (
      <p className="hint" data-testid="export-download-unavailable">
        校验未通过时不提供下载。
      </p>
    )
  }
  return (
    <div className="ndr-export-download" data-testid="export-download">
      <a
        className="ndr-button ndr-primary"
        href={exportDownloadUrl(artifact.id)}
        download
        data-testid="export-download-link"
      >
        下载 {artifact.filename}
      </a>
      <p className="hint">
        sha256 {artifact.file_sha256?.slice(0, 12)}… · 文件已经保存在数据目录里，
        重复下载不会重新生成（相同快照 + 格式 + 样式直接复用）。
      </p>
    </div>
  )
}
