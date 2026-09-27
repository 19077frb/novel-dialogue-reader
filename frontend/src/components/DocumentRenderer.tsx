import type { ReactNode } from 'react'

import { resourceUrl } from '../api/books'
import type { ContentNodeOut, RubyAnnotation } from '../api/types'
import { nodePayload } from '../api/types'
import { AnnotationLayer } from './AnnotationLayer'

/** 候选引语范围（来自扫描器，只表示“这里有一段引号内容”，不含说话人）。 */
export interface CandidateRange {
  quoteId: string
  startCp: number
  endCp: number
  nestingDepth?: number
}

export interface DocumentRendererProps {
  bookId: string
  nodes: ContentNodeOut[]
  /** 候选引语覆盖：只做提示性标记，不表示任何识别结果。 */
  candidates?: CandidateRange[]
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

interface CandidateNode {
  range: CandidateRange
  start: number
  end: number
  children: CandidateNode[]
}

/** 候选范围可能嵌套（『』在「」里），按包含关系建树后逐层渲染。 */
export function buildCandidateTree(ranges: CandidateRange[]): CandidateNode[] {
  const sorted = [...ranges].sort((a, b) => a.startCp - b.startCp || b.endCp - a.endCp)
  const roots: CandidateNode[] = []
  const stack: CandidateNode[] = []
  for (const range of sorted) {
    const node: CandidateNode = {
      range,
      start: range.startCp,
      end: range.endCp,
      children: [],
    }
    while (stack.length > 0 && stack[stack.length - 1].end <= node.start) stack.pop()
    if (stack.length === 0) roots.push(node)
    else stack[stack.length - 1].children.push(node)
    stack.push(node)
  }
  return roots
}

function renderNodes(
  tree: CandidateNode[],
  text: string,
  baseCp: number,
  ruby: RubyAnnotation[] | undefined,
  from: number,
  to: number,
): ReactNode[] {
  const parts: ReactNode[] = []
  let cursor = from
  for (const node of tree) {
    if (node.end <= cursor || node.start >= to) continue
    const start = Math.max(cursor, node.start)
    if (start > cursor) {
      parts.push(renderTextWithRuby(text.slice(cursor - baseCp, start - baseCp), cursor, ruby))
    }
    const end = Math.min(to, node.end)
    parts.push(
      <span
        key={`${node.start}-${node.end}`}
        className="ndr-candidate"
        data-testid="candidate-quote"
        data-quote-id={node.range.quoteId}
        data-start-cp={node.start}
        data-end-cp={node.end}
        title="扫描器提出的候选引语（尚未判定说话人）"
      >
        {renderNodes(node.children, text, baseCp, ruby, start, end)}
      </span>,
    )
    cursor = end
  }
  if (cursor < to) {
    parts.push(renderTextWithRuby(text.slice(cursor - baseCp, to - baseCp), cursor, ruby))
  }
  return parts
}

function NodeView({
  node,
  bookId,
  candidates,
  onNodeClick,
}: {
  node: ContentNodeOut
  bookId: string
  candidates: CandidateRange[]
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

  const nodeEnd = node.start_cp + node.text.length
  const relevant = candidates.filter(
    (range) => range.startCp < nodeEnd && range.endCp > node.start_cp,
  )
  const text =
    relevant.length > 0
      ? renderNodes(
          buildCandidateTree(relevant),
          node.text,
          node.start_cp,
          payload.ruby,
          node.start_cp,
          nodeEnd,
        )
      : renderTextWithRuby(node.text, node.start_cp, payload.ruby)

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
 * `candidates` 只画出扫描器找到的引号范围（虚线标记），不表示任何说话人判断；
 * 颜色/编号属于 T05 之后的标注投影（`AnnotationLayer`）。
 */
export function DocumentRenderer({
  bookId,
  nodes,
  candidates = [],
  onNodeClick,
}: DocumentRendererProps) {
  return (
    <AnnotationLayer>
      <div className="ndr-document" data-testid="document-renderer">
        {nodes.map((node) => (
          <NodeView
            key={nodeKey(node)}
            node={node}
            bookId={bookId}
            candidates={candidates}
            onNodeClick={onNodeClick}
          />
        ))}
      </div>
    </AnnotationLayer>
  )
}