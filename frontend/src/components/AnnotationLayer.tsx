import type { ReactNode } from 'react'

import type { AnnotationItemOut } from '../api/types'
import { colorForIndex, labelText } from '../styles/palette'

/**
 * 着色/编号层的**数据契约**：颜色与编号全部来自后端标注投影。
 *
 * - 颜色与编号只来自后端 `GET /api/books/{id}/annotations`；没有标注就不显示任何颜色或编号。
 * - `withheld=true`（初读 horizon 之下的后文证据）不下发颜色与编号。
 * - 编号是真实文本节点（如 `〔S1〕`），不是 CSS 伪元素，灰度或覆盖颜色时仍可辨认。
 *
 * 渲染由 DocumentRenderer 在节点内分片完成，本组件负责容器与计数。
 */
export interface AnnotationLayerProps {
  annotations?: AnnotationItemOut[]
  children?: ReactNode
}

export function AnnotationLayer({ annotations = [], children }: AnnotationLayerProps) {
  return (
    <div
      className="ndr-annotation-layer"
      data-testid="annotation-layer"
      data-annotation-count={annotations.length}
    >
      {children}
    </div>
  )
}

/** 取某个标注的可见颜色（不可见/无分组时为 undefined）。 */
export function annotationColor(annotation: AnnotationItemOut): string | undefined {
  if (annotation.withheld) return undefined
  return colorForIndex(annotation.color_index)
}

/** 标注在视觉上是否要显示（不可见的不着色）。 */
export function annotationVisible(annotation: AnnotationItemOut): boolean {
  return !annotation.withheld && (annotation.color_index !== null || annotation.status === 'UNKNOWN')
}

export { colorForIndex, labelText }