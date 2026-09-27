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
export type JobOut = components['schemas']['JobOut']
export type ReadingProgressIn = components['schemas']['ReadingProgressIn']
export type ReadingProgressOut = components['schemas']['ReadingProgressOut']
export type CursorPageBook = components['schemas']['CursorPage_BookOut_']
export type QuoteOut = components['schemas']['QuoteOut']
export type GapOut = components['schemas']['GapOut']
export type QuoteDetailOut = components['schemas']['QuoteDetailOut']
export type CursorPageQuote = components['schemas']['CursorPage_QuoteOut_']

export type BookFormat = BookOut['format']
export type ContentNodeType = ContentNodeOut['node_type']
export type ReadingMode = components['schemas']['ReadingMode']
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