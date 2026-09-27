import type { ReactNode } from 'react'

import { resourceUrl } from '../api/books'
import type { ContentNodeOut, RubyAnnotation } from '../api/types'
import { nodePayload } from '../api/types'
import { AnnotationLayer } from './AnnotationLayer'

export interface DocumentRendererProps {
  bookId: string
  nodes: ContentNodeOut[]
  /** 点击节点时的回调：T13 的普通对白详情入口，现在只用于定位。 */
  onNodeClick?: (node: ContentNodeOut) => void
}

function nodeKey(node: ContentNodeOut): string {
  return `${node.chapter_id ?? 'none'}:${node.node_id}:${node.start_cp}`
}

/** 把正文按 ruby 注解切分；注音只在 <rt> 里，不参与纯文本。 */
function renderTextWithRuby(
  text: string,
  baseCp: number,
  ruby: RubyAnnotation[] | undefined,
): ReactNode {
  if (!ruby || ruby.length === 0) return text

  const parts: ReactNode[] = []
  let cursor = 0
  const sorted = [...ruby].sort((a, b) => a.start_cp - b.start_cp)
  for (const annotation of sorted) {
    const start = Math.max(0, Math.min(text.length, annotation.start_cp - baseCp))
    const end = Math.max(start, Math.min(text.length, annotation.end_cp - baseCp))
    if (start > cursor) parts.push(text.slice(cursor, start))
    const base = text.slice(start, end)
    if (base) {
      parts.push(
        <ruby key={`${start}-${end}`}>
          {base}
          <rp>(</rp>
          <rt>{annotation.rt}</rt>
          <rp>)</rp>
        </ruby>,
      )
    }
    cursor = end
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return parts
}

function NodeView({
  node,
  bookId,
  onNodeClick,
}: {
  node: ContentNodeOut
  bookId: string
  onNodeClick?: (node: ContentNodeOut) => void
}) {
  const payload = nodePayload(node)
  const common = {
    className: `ndr-node ndr-node-${node.node_type}`,
    'data-testid': 'ndr-node',
    'data-node-id': node.node_id,
    'data-node-type': node.node_type,
    'data-start-cp': node.start_cp,
    'data-end-cp': node.end_cp,
    onClick: onNodeClick ? () => onNodeClick(node) : undefined,
  } as const

  if (node.node_type === 'image') {
    const resourceId = payload.resource_id
    if (!resourceId) {
      return (
        <div {...common}>
          <p className="ndr-missing-image">（插图缺失）</p>
        </div>
      )
    }
    return (
      <figure {...common}>
        <img
          src={resourceUrl(bookId, resourceId)}
          alt={payload.alt ?? ''}
          loading="lazy"
          className="ndr-image"
        />
      </figure>
    )
  }

  if (node.node_type === 'separator') {
    return (
      <div {...common}>
        <hr className="ndr-separator" />
      </div>
    )
  }

  const text = renderTextWithRuby(node.text, node.start_cp, payload.ruby)
  if (node.node_type === 'heading') {
    const level = Math.min(Math.max(payload.level ?? 1, 1), 3)
    const Heading = (['h2', 'h3', 'h4'] as const)[level - 1]
    return (
      <div {...common}>
        <Heading className="ndr-heading">{text}</Heading>
      </div>
    )
  }

  return (
    <div {...common}>
      <p>{text}</p>
    </div>
  )
}

/**
 * 结构化正文渲染：后端给节点与码点范围，前端只负责排版。
 * 不做任何识别结果的着色（那属于 AnnotationLayer / T05 起）。
 */
export function DocumentRenderer({ bookId, nodes, onNodeClick }: DocumentRendererProps) {
  return (
    <AnnotationLayer>
      <div className="ndr-document" data-testid="document-renderer">
        {nodes.map((node) => (
          <NodeView key={nodeKey(node)} node={node} bookId={bookId} onNodeClick={onNodeClick} />
        ))}
      </div>
    </AnnotationLayer>
  )
}