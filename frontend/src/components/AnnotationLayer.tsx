import type { ReactNode } from 'react'

/**
 * 着色/编号层的**预留位置**（T05 起接入真实标注投影）。
 *
 * 当前不渲染任何识别结果：没有标注就不能凭空显示颜色、编号或人物名。
 * 约定的接入方式：后端 `GET /api/books/{id}/annotations`（T11/T15）返回按 quote 的投影，
 * 本组件按 `start_cp/end_cp` 在 `DocumentRenderer` 的节点内分片着色；
 * 节点上的 `data-node-id` / `data-start-cp` / `data-end-cp` 是给它的定位接口。
 */
export interface AnnotationLayerProps {
  children?: ReactNode
}

export function AnnotationLayer({ children }: AnnotationLayerProps) {
  return (
    <div className="ndr-annotation-layer" data-testid="annotation-layer" data-annotation-count="0">
      {children}
    </div>
  )
}