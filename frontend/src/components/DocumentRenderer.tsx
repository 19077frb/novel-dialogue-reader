import type { ReactNode } from 'react'

import { resourceUrl } from '../api/books'
import type { AnnotationItemOut, ContentNodeOut, RubyAnnotation } from '../api/types'
import { nodePayload } from '../api/types'
import { cpLength, sliceByCodepoints, utf16IndexForCp } from '../text/codepoints'
import { annotationColor, AnnotationLayer, labelText } from './AnnotationLayer'

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
  /**
   * 有效标注投影（来自 `GET /api/books/{id}/annotations`）。
   * 只有这里的颜色/编号才会显示；`withheld` 不下发颜色与编号（不提前泄漏后文证据）。
   */
  annotations?: AnnotationItemOut[]
  /** 点击节点时的回调：用于定位（保存阅读位置）。 */
  onNodeClick?: (node: ContentNodeOut) => void
  /** 点击某段引语：普通对白详情入口（打开确认抽屉）。 */
  onQuoteClick?: (quoteId: string) => void
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
    // 码点 → UTF-16 下标：astral 字符（emoji/扩展汉字）不能被当成 2 个码点
    const start = utf16IndexForCp(text, baseCp, annotation.start_cp)
    const end = Math.max(start, utf16IndexForCp(text, baseCp, annotation.end_cp))
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

export interface AnnotationSlice {
  annotation: AnnotationItemOut | null
  start: number
  end: number
}

/**
 * 按标注边界把绝对码点区间 `[from, to)` 切成不重叠的小块。
 * 同一位置多层嵌套时取跨度最小者（最内层标注）着色。纯函数，便于测试。
 */
export function sliceByAnnotations(
  annotations: AnnotationItemOut[],
  from: number,
  to: number,
): AnnotationSlice[] {
  const relevant = annotations.filter(
    (item) => item.start_cp < to && item.end_cp > from && item.end_cp > item.start_cp,
  )
  if (relevant.length === 0) return [{ annotation: null, start: from, end: to }]

  const points = new Set<number>([from, to])
  for (const item of relevant) {
    points.add(Math.max(from, item.start_cp))
    points.add(Math.min(to, item.end_cp))
  }
  const ordered = [...points].sort((a, b) => a - b)
  const slices: AnnotationSlice[] = []
  for (let index = 0; index < ordered.length - 1; index += 1) {
    const start = ordered[index]
    const end = ordered[index + 1]
    if (end <= start) continue
    let chosen: AnnotationItemOut | null = null
    for (const item of relevant) {
      if (item.start_cp <= start && item.end_cp >= end) {
        if (chosen === null || item.end_cp - item.start_cp < chosen.end_cp - chosen.start_cp) {
          chosen = item
        }
      }
    }
    slices.push({ annotation: chosen, start, end })
  }
  return slices
}

/**
 * 渲染一段纯文本，按标注边界分片着色。
 * 编号是真实文本节点（`〔S1〕`），只在标注起点出现一次；跨节点的同一引语会分成多个 span，
 * 但共享同一 `data-quote-id`。
 */
function renderAnnotatedText(
  text: string,
  baseCp: number,
  ruby: RubyAnnotation[] | undefined,
  annotations: AnnotationItemOut[],
  from: number,
  to: number,
  onQuoteClick?: (quoteId: string) => void,
): ReactNode[] {
  const parts: ReactNode[] = []
  for (const slice of sliceByAnnotations(annotations, from, to)) {
    const segment = sliceByCodepoints(text, baseCp, slice.start, slice.end)
    const inner = renderTextWithRuby(segment, slice.start, ruby)
    const annotation = slice.annotation
    if (annotation === null || annotation.withheld) {
      parts.push(inner)
      continue
    }
    const color = annotationColor(annotation)
    const label = annotation.label
    const showLabel = Boolean(label) && slice.start === annotation.start_cp
    parts.push(
      <span
        key={`${annotation.quote_id}-${slice.start}`}
        className={color ? 'ndr-annotation' : 'ndr-annotation ndr-annotation-unknown'}
        data-testid="annotation-span"
        data-quote-id={annotation.quote_id}
        data-status={annotation.status}
        data-stale={annotation.stale ? 'true' : 'false'}
        data-label={label ?? ''}
        style={color ? { color } : undefined}
        onClick={
          onQuoteClick
            ? (event) => {
                event.stopPropagation()
                onQuoteClick(annotation.quote_id)
              }
            : undefined
        }
        title={
          annotation.status === 'UNKNOWN'
            ? '证据不足：不指定说话人（无色无编号）'
            : `说话人分组 ${label ?? ''}（${annotation.status}）`
        }
      >
        {showLabel ? (
          <span className="ndr-annotation-label" data-testid="annotation-label">
            {labelText(label)}
          </span>
        ) : null}
        {inner}
      </span>,
    )
  }
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
  annotations: AnnotationItemOut[],
  from: number,
  to: number,
  onQuoteClick?: (quoteId: string) => void,
): ReactNode[] {
  const parts: ReactNode[] = []
  let cursor = from
  for (const node of tree) {
    if (node.end <= cursor || node.start >= to) continue
    const start = Math.max(cursor, node.start)
    if (start > cursor) {
      parts.push(
        ...renderAnnotatedText(text, baseCp, ruby, annotations, cursor, start, onQuoteClick),
      )
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
        onClick={
          onQuoteClick
            ? (event) => {
                event.stopPropagation()
                onQuoteClick(node.range.quoteId)
              }
            : undefined
        }
      >
        {renderNodes(node.children, text, baseCp, ruby, annotations, start, end, onQuoteClick)}
      </span>,
    )
    cursor = end
  }
  if (cursor < to) {
    parts.push(...renderAnnotatedText(text, baseCp, ruby, annotations, cursor, to, onQuoteClick))
  }
  return parts
}

function NodeView({
  node,
  bookId,
  candidates,
  annotations,
  onNodeClick,
  onQuoteClick,
}: {
  node: ContentNodeOut
  bookId: string
  candidates: CandidateRange[]
  annotations: AnnotationItemOut[]
  onNodeClick?: (node: ContentNodeOut) => void
  onQuoteClick?: (quoteId: string) => void
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

  // 节点范围以后端为准（`end_cp` 是码点）；没有可信范围时才退回按码点计数
  const nodeEnd =
    node.end_cp > node.start_cp ? node.end_cp : node.start_cp + cpLength(node.text)
  const relevant = candidates.filter(
    (range) => range.startCp < nodeEnd && range.endCp > node.start_cp,
  )
  const relevantAnnotations = annotations.filter(
    (item) => item.start_cp < nodeEnd && item.end_cp > node.start_cp,
  )
  const text =
    relevant.length > 0
      ? renderNodes(
          buildCandidateTree(relevant),
          node.text,
          node.start_cp,
          payload.ruby,
          relevantAnnotations,
          node.start_cp,
          nodeEnd,
          onQuoteClick,
        )
      : renderAnnotatedText(
          node.text,
          node.start_cp,
          payload.ruby,
          relevantAnnotations,
          node.start_cp,
          nodeEnd,
          onQuoteClick,
        )

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
 * `annotations` 才是后端下发的有效投影：颜色与编号只来自它，`withheld` 的不着色。
 */
export function DocumentRenderer({
  bookId,
  nodes,
  candidates = [],
  annotations = [],
  onNodeClick,
  onQuoteClick,
}: DocumentRendererProps) {
  return (
    <AnnotationLayer annotations={annotations}>
      <div className="ndr-document" data-testid="document-renderer">
        {nodes.map((node) => (
          <NodeView
            key={nodeKey(node)}
            node={node}
            bookId={bookId}
            candidates={candidates}
            annotations={annotations}
            onNodeClick={onNodeClick}
            onQuoteClick={onQuoteClick}
          />
        ))}
      </div>
    </AnnotationLayer>
  )
}