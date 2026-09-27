/**
 * API 类型：直接取自后端生成的 OpenAPI 类型（单一事实来源，不维护第二份枚举）。
 * 生成命令：npm --prefix frontend run generate:api
 */
import type { components } from './schema'

export type BookOut = components['schemas']['BookOut']
export type BookVersionOut = components['schemas']['BookVersionOut']
export type ChapterOut = components['schemas']['ChapterOut']
export type ContentNodeOut = components['schemas']['ContentNodeOut']
export type ContentResponse = components['schemas']['ContentResponse']
export type ImportResult = components['schemas']['ImportResult']
export type JobDetailOut = components['schemas']['JobDetailOut']
export type JobRunOut = components['schemas']['JobRunOut']
export type EstimateOut = components['schemas']['EstimateOut']
export type UsageOut = components['schemas']['UsageOut']
export type AnnotationsResponse = components['schemas']['AnnotationsResponse']
export type AnnotationItemOut = components['schemas']['AnnotationItemOut']
export type SpeakerLegendItemOut = components['schemas']['SpeakerLegendItemOut']
export type AnnotationCountsOut = components['schemas']['AnnotationCountsOut']
export type AnnotationStateOut = components['schemas']['AnnotationStateOut']
export type ReviewItemOut = components['schemas']['ReviewItemOut']
export type ReviewItemDetailOut = components['schemas']['ReviewItemDetailOut']
export type ReviewQueueResponse = components['schemas']['ReviewQueueResponse']
export type ReviewItemCountsOut = components['schemas']['ReviewItemCountsOut']
export type ReviewReason = components['schemas']['ReviewReason']
export type ReviewQueueStatus = components['schemas']['ReviewQueueStatus']
export type CorrectionOut = components['schemas']['CorrectionOut']
export type GapCorrectionOut = components['schemas']['GapCorrectionOut']
export type SpeakerRevisionOut = components['schemas']['SpeakerRevisionOut']
export type UndoOut = components['schemas']['UndoOut']
export type SceneGroupRefOut = components['schemas']['SceneGroupRefOut']
export type RecheckIn = components['schemas']['RecheckIn']
export type ExportPreviewOut = components['schemas']['ExportPreviewOut']
export type ExportArtifactOut = components['schemas']['ExportArtifactOut']
export type ExportStylePreset = components['schemas']['ExportStylePreset']
export type ExportFormat = components['schemas']['ExportFormat']
export type VisibilityPolicy = components['schemas']['VisibilityPolicy']
export type JobRecoveryOut = components['schemas']['JobRecoveryOut']
export type RecoveryActionOut = components['schemas']['RecoveryActionOut']
export type ReadingProgressIn = components['schemas']['ReadingProgressIn']
export type ReadingProgressOut = components['schemas']['ReadingProgressOut']
export type CursorPageBook = components['schemas']['CursorPage_BookOut_']
export type QuoteOut = components['schemas']['QuoteOut']
export type GapOut = components['schemas']['GapOut']
export type QuoteDetailOut = components['schemas']['QuoteDetailOut']
export type CursorPageQuote = components['schemas']['CursorPage_QuoteOut_']
export type CursorPageGap = components['schemas']['CursorPage_GapOut_']
export type ModelProfileOut = components['schemas']['ModelProfileOut']
export type ModelProfileCreate = components['schemas']['ModelProfileCreate']
export type ModelProfilePatch = components['schemas']['ModelProfilePatch']
export type ProtocolCapabilitiesOut = components['schemas']['ProtocolCapabilitiesOut']
export type CredentialMode = components['schemas']['CredentialMode']
export type ConnectionTestIn = components['schemas']['ConnectionTestIn']
export type ConnectionTestOut = components['schemas']['ConnectionTestOut']

export type BookFormat = BookOut['format']
export type ContentNodeType = ContentNodeOut['node_type']
export type ReadingMode = components['schemas']['ReadingMode']
export type GapDecision = components['schemas']['GapDecision']
export type QuoteKind = components['schemas']['QuoteKind']
export type ImportStatus = BookOut['import_status']

/** 节点 payload：标题层级、图片资源、ruby 注音（rt 不进入正文）。 */
export interface RubyAnnotation {
  start_cp: number
  end_cp: number
  base: string
  rt: string
}

export interface NodePayload {
  text?: string
  level?: number
  resource_id?: string
  media_type?: string
  alt?: string
  ruby?: RubyAnnotation[]
  separator?: string
}

export function nodePayload(node: ContentNodeOut): NodePayload {
  return (node.payload ?? {}) as NodePayload
}