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
        校验通过后即可下载；请查看校验结果。
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
        相同内容、格式和样式可直接复用已生成的文件。
      </p>
    </div>
  )
}
