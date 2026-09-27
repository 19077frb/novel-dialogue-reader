/**
 * 导出（T15A 后端契约，T15B 界面）。
 *
 * 预览会**冻结快照**（不调用模型）；生成按快照幂等执行；下载走受控端点。
 */
import { apiData } from './client'
import type {
  ExportArtifactOut,
  ExportFormat,
  ExportPreviewOut,
  ExportStylePreset,
  VisibilityPolicy,
} from './types'

export const exportKeys = {
  artifact: (artifactId: string) => ['export-artifact', artifactId] as const,
}

export interface ExportStyleInput {
  preset: ExportStylePreset
  paletteId?: string
}

export interface ExportPreviewInput {
  chapterIds?: string[] | null
  visibilityPolicy?: VisibilityPolicy
  style?: ExportStyleInput
}

function stylePayload(style: ExportStyleInput | undefined) {
  return {
    preset: style?.preset ?? 'color_and_label',
    palette_id: style?.paletteId ?? 'reader-default',
  }
}

/** 冻结一次导出快照并取后端样张（不调用模型）。 */
export function previewExport(
  bookId: string,
  input: ExportPreviewInput = {},
  signal?: AbortSignal,
): Promise<ExportPreviewOut> {
  return apiData<ExportPreviewOut>(`/api/books/${bookId}/exports/preview`, {
    method: 'POST',
    signal,
    body: {
      book_version_id: null,
      chapter_ids: input.chapterIds ?? null,
      visibility_policy: input.visibilityPolicy ?? 'position_safe',
      style: stylePayload(input.style),
    },
  })
}

export interface CreateExportInput {
  snapshotId: string
  format: ExportFormat
  style?: ExportStyleInput
  idempotencyKey: string
}

export function createExport(
  bookId: string,
  input: CreateExportInput,
  signal?: AbortSignal,
): Promise<ExportArtifactOut> {
  return apiData<ExportArtifactOut>(`/api/books/${bookId}/exports`, {
    method: 'POST',
    signal,
    body: {
      snapshot_id: input.snapshotId,
      format: input.format,
      style: stylePayload(input.style),
      idempotency_key: input.idempotencyKey,
    },
  })
}

export function fetchExportArtifact(
  artifactId: string,
  signal?: AbortSignal,
): Promise<ExportArtifactOut> {
  return apiData<ExportArtifactOut>(`/api/exports/${artifactId}`, { signal })
}

/** 受控下载地址（浏览器用 `<a download>` 触发；后端给出安全的文件名）。 */
export function exportDownloadUrl(artifactId: string): string {
  return `/api/exports/${artifactId}/download`
}