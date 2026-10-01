import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { AnnotationItemOut, ContentNodeOut } from '../src/api/types'
import { DocumentRenderer, sliceByAnnotations } from '../src/components/DocumentRenderer'

function node(
  partial: Partial<ContentNodeOut> & Pick<ContentNodeOut, 'node_id' | 'node_type'>,
): ContentNodeOut {
  return {
    ordinal: 0,
    start_cp: 0,
    end_cp: 0,
    chapter_id: 'ch1',
    chapter_ordinal: 0,
    text: '',
    payload: {},
    ...partial,
  } as ContentNodeOut
}

describe('DocumentRenderer', () => {
  it('多句段落只提供一个书签，保存段首且不触发正文点击', () => {
    const save = vi.fn()
    const click = vi.fn()
    render(<DocumentRenderer bookId="b1" nodes={[node({ node_id: 'sentence', node_type: 'paragraph', start_cp: 10, end_cp: 24, text: '「😀你好。第二句！」尾句' })]} onBookmark={save} onNodeClick={click} />)
    const buttons = screen.getAllByRole('button', { name: /保存书签/ })
    expect(buttons).toHaveLength(1)
    fireEvent.click(buttons[0])
    expect(save).toHaveBeenCalledWith(10, '「😀你好。第二句！」尾句')
    expect(click).not.toHaveBeenCalled()
    expect(screen.getByTestId('document-renderer')).toHaveTextContent('「😀你好。')
    expect(screen.getByTestId('document-renderer').textContent).toBe('「😀你好。第二句！」尾句')
  })
  it('每个非空段落提供一个入口，不给标题、空段落或图片提供入口', () => {
    const save = vi.fn()
    render(<DocumentRenderer bookId="b1" bookmarkPending onBookmark={save} nodes={[
      node({ node_id: 'title', node_type: 'heading', text: '标题' }),
      node({ node_id: 'p1', node_type: 'paragraph', start_cp: 3, end_cp: 10, text: '第一句。第二句。' }),
      node({ node_id: 'p2', node_type: 'paragraph', start_cp: 11, end_cp: 14, text: '无标点段落' }),
      node({ node_id: 'empty', node_type: 'paragraph', text: '  ' }),
      node({ node_id: 'image', node_type: 'image', payload: { resource_id: 'img' } }),
    ]} />)
    const buttons = screen.getAllByRole('button', { name: /保存书签/ })
    expect(buttons).toHaveLength(2)
    buttons.forEach(button => expect(button).toBeDisabled())
  })
  it('按节点类型渲染标题、段落、分隔符，并带上定位属性', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[
          node({ node_id: 'n0', node_type: 'heading', start_cp: 0, end_cp: 5, text: '第一章', payload: { level: 1 } }),
          node({ node_id: 'n1', node_type: 'paragraph', start_cp: 6, end_cp: 12, text: '「你好。」' }),
          node({ node_id: 'n2', node_type: 'separator', start_cp: 13, end_cp: 13, text: '' }),
        ]}
      />,
    )

    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('第一章')
    expect(screen.getByText('「你好。」')).toBeInTheDocument()

    const first = screen.getAllByTestId('ndr-node')[0]
    expect(first).toHaveAttribute('data-node-id', 'n0')
    expect(first).toHaveAttribute('data-start-cp', '0')
    expect(first).toHaveAttribute('data-end-cp', '5')
    expect(document.querySelector('hr.ndr-separator')).not.toBeNull()
  })

  it('ruby 注音放在 rt 里，基底文字留在正文', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[
          node({
            node_id: 'n1',
            node_type: 'paragraph',
            start_cp: 10,
            end_cp: 18,
            text: '漢字与「对白」。',
            payload: { ruby: [{ start_cp: 10, end_cp: 11, base: '漢', rt: 'かん' }] },
          }),
        ]}
      />,
    )

    expect(document.querySelector('rt')?.textContent).toBe('かん')
    expect(document.querySelector('ruby')?.textContent?.startsWith('漢')).toBe(true)
  })

  it('图片节点通过受控资源地址加载，并使用 alt', () => {
    render(
      <DocumentRenderer
        bookId="book-1"
        nodes={[
          node({
            node_id: 'n0',
            node_type: 'image',
            start_cp: 15,
            end_cp: 15,
            text: '',
            payload: { resource_id: 'r0001', media_type: 'image/png', alt: '插图' },
          }),
        ]}
      />,
    )

    const image = screen.getByRole('img') as HTMLImageElement
    expect(image.getAttribute('src')).toBe('/api/books/book-1/resources/r0001')
    expect(image.getAttribute('alt')).toBe('插图')
  })

  it('候选引语只画虚线标记，不带任何说话人信息', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[
          node({
            node_id: 'n0',
            node_type: 'paragraph',
            start_cp: 100,
            end_cp: 110,
            text: '「雨停了。」少女说。',
          }),
        ]}
        candidates={[{ quoteId: 'q1', startCp: 100, endCp: 106, nestingDepth: 0 }]}
      />,
    )

    const mark = screen.getByTestId('candidate-quote')
    expect(mark).toHaveTextContent('「雨停了。」')
    expect(mark).toHaveAttribute('data-quote-id', 'q1')
    expect(mark.className).toContain('ndr-candidate')
    // 覆盖显示不等于识别：没有颜色/编号/人物名
    expect(mark.getAttribute('data-speaker')).toBeNull()
    expect(screen.getByText(/少女说/)).toBeInTheDocument()
  })

  it('嵌套候选渲染为嵌套标记', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[
          node({
            node_id: 'n0',
            node_type: 'paragraph',
            start_cp: 0,
            end_cp: 13,
            text: '「他说『明天见』。」',
          }),
        ]}
        candidates={[
          { quoteId: 'outer', startCp: 0, endCp: 11, nestingDepth: 0 },
          { quoteId: 'inner', startCp: 3, endCp: 8, nestingDepth: 1 },
        ]}
      />,
    )

    const marks = screen.getAllByTestId('candidate-quote')
    expect(marks).toHaveLength(2)
    const outer = screen.getAllByTestId('candidate-quote')[0]
    const inner = within(outer).getAllByTestId('candidate-quote')[0]
    expect(outer).toHaveAttribute('data-quote-id', 'outer')
    expect(inner).toHaveAttribute('data-quote-id', 'inner')
    expect(inner).toHaveTextContent('『明天见』')
  })

  it('没有候选时不产生任何标记', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node({ node_id: 'n0', node_type: 'paragraph', start_cp: 0, end_cp: 3, text: '正文。' })]}
      />,
    )
    expect(screen.queryAllByTestId('candidate-quote')).toHaveLength(0)
  })

  it('没有标注时不渲染任何着色或编号（AnnotationLayer 只是占位）', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node({ node_id: 'n0', node_type: 'paragraph', start_cp: 0, end_cp: 3, text: '正文。' })]}
      />,
    )

    const layer = screen.getByTestId('annotation-layer')
    expect(layer).toHaveAttribute('data-annotation-count', '0')
    expect(layer.querySelectorAll('[class*="speaker"]')).toHaveLength(0)
    expect(layer.querySelectorAll('[class*="quote"]')).toHaveLength(0)
  })
})

describe('DocumentRenderer 标注投影', () => {
  const node = {
    node_id: 'n0',
    node_type: 'paragraph' as const,
    ordinal: 0,
    start_cp: 100,
    end_cp: 116,
    chapter_id: 'ch1',
    chapter_ordinal: 0,
    text: '「雨停了。」少女合上伞。',
    payload: {},
  }

  function annotation(partial: Partial<AnnotationItemOut>): AnnotationItemOut {
    return {
      quote_id: 'q1',
      scene_id: 's1',
      start_cp: 100,
      end_cp: 106,
      kind: 'speech',
      assignment: 'EXISTING',
      basis: 'DIRECT',
      status: 'ACCEPTED',
      source: 'MODEL',
      speaker_group_id: 'g1',
      label: 'S1',
      speaker_description: '戴着红围巾的女同学',
      color_index: 0,
      stale: false,
      user_locked: false,
      withheld: false,
      ...partial,
    } as AnnotationItemOut
  }

  it('可见标注按范围着色，并把编号渲染成真实文本节点', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node]}
        annotations={[annotation({})]}
      />,
    )

    const span = screen.getByTestId('annotation-span')
    expect(span).toHaveAttribute('data-quote-id', 'q1')
    expect(span).toHaveAttribute('data-status', 'ACCEPTED')
    expect(span).toHaveAttribute('title', 'S1：戴着红围巾的女同学')
    expect(span.style.color).toBeTruthy()
    // 编号是真实文本节点，不是 CSS 伪元素
    expect(within(span).getByTestId('annotation-label')).toHaveTextContent('〔S1〕')
    // 着色不改变原文
    expect(screen.getByTestId('document-renderer')).toHaveTextContent('「雨停了。」少女合上伞。')
    // 只给引语着色，引语外的叙述不受影响
    expect(span).toHaveTextContent('「雨停了。」')
  })

  it('withheld 的标注既不着色也不下发编号', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node]}
        annotations={[annotation({ withheld: true, label: null, color_index: null })]}
      />,
    )

    expect(screen.queryByTestId('annotation-span')).toBeNull()
    expect(screen.queryByTestId('annotation-label')).toBeNull()
    expect(screen.getByTestId('document-renderer')).toHaveTextContent('「雨停了。」少女合上伞。')
  })

  it('UNKNOWN 标注不给颜色也不给编号', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node]}
        annotations={[
          annotation({
            status: 'UNKNOWN',
            assignment: 'UNKNOWN',
            basis: 'INSUFFICIENT',
            speaker_group_id: null,
            label: null,
            color_index: null,
          }),
        ]}
      />,
    )

    const span = screen.getByTestId('annotation-span')
    expect(span.className).toContain('ndr-annotation-unknown')
    expect(span.style.color).toBe('')
    expect(screen.queryByTestId('annotation-label')).toBeNull()
  })

  it('跨节点的同一引语分成多个 span，但只在起点出现一次编号', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[
          {
            ...node,
            node_id: 'n0',
            start_cp: 0,
            end_cp: 4,
            text: '「跨块',
          },
          {
            ...node,
            node_id: 'n1',
            start_cp: 4,
            end_cp: 10,
            text: '的同一句。」',
          },
        ]}
        annotations={[annotation({ start_cp: 0, end_cp: 10 })]}
      />,
    )

    const spans = screen.getAllByTestId('annotation-span')
    expect(spans).toHaveLength(2)
    expect(spans[0]).toHaveAttribute('data-quote-id', 'q1')
    expect(spans[1]).toHaveAttribute('data-quote-id', 'q1')
    expect(screen.getAllByTestId('annotation-label')).toHaveLength(1)
    expect(spans[0]).toHaveTextContent('〔S1〕')
    expect(spans[1]).toHaveTextContent('的同一句。」')
  })

  it('sliceByAnnotations 在嵌套时取最内层标注', () => {
    const outer = annotation({ quote_id: 'outer', start_cp: 0, end_cp: 20, label: 'S1' })
    const inner = annotation({
      quote_id: 'inner',
      start_cp: 5,
      end_cp: 10,
      label: 'S2',
      color_index: 1,
    })

    const slices = sliceByAnnotations([outer, inner], 0, 20)

    expect(slices.map((slice) => [slice.start, slice.end, slice.annotation?.quote_id])).toEqual([
      [0, 5, 'outer'],
      [5, 10, 'inner'],
      [10, 20, 'outer'],
    ])
  })

  it('没有标注时不着色（原文与候选覆盖行为不变）', () => {
    render(<DocumentRenderer bookId="b1" nodes={[node]} candidates={[
      { quoteId: 'q1', startCp: 100, endCp: 106 },
    ]} />)

    expect(screen.queryByTestId('annotation-span')).toBeNull()
    expect(screen.getByTestId('candidate-quote')).toHaveTextContent('「雨停了。」')
  })
})


describe('DocumentRenderer 码点定位', () => {
  const astralText = '𠮷野家的猫🐈跳上窗台。' // 11 码点 / 13 UTF-16 单元
  const base = 500

  function astralNode(): ContentNodeOut {
    return {
      node_id: 'a0',
      node_type: 'paragraph',
      ordinal: 0,
      start_cp: base,
      end_cp: base + 11,
      chapter_id: 'ch1',
      chapter_ordinal: 0,
      text: astralText,
      payload: {},
    } as ContentNodeOut
  }

  function annotation(partial: Partial<AnnotationItemOut>): AnnotationItemOut {
    return {
      quote_id: 'qa',
      scene_id: 's1',
      start_cp: base,
      end_cp: base + 1,
      kind: 'speech',
      assignment: 'EXISTING',
      basis: 'DIRECT',
      status: 'ACCEPTED',
      source: 'MODEL',
      speaker_group_id: 'g1',
      label: 'S1',
      color_index: 0,
      stale: false,
      user_locked: false,
      withheld: false,
      ...partial,
    } as AnnotationItemOut
  }

  it('扩展汉字（U+20BB7）按码点着色，不会切到半个代理对', () => {
    render(<DocumentRenderer bookId="b1" nodes={[astralNode()]} annotations={[annotation({})]} />)

    const span = screen.getByTestId('annotation-span')
    expect(span).toHaveTextContent('𠮷')
    expect(span.textContent).toBe('〔S1〕𠮷')
  })

  it('emoji 在码点 5 上：着色范围正好覆盖 🐈 而不含相邻汉字', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[astralNode()]}
        annotations={[annotation({ start_cp: base + 5, end_cp: base + 6, label: 'S2', color_index: 1 })]}
      />,
    )

    const span = screen.getByTestId('annotation-span')
    expect(span.textContent).toBe('〔S2〕🐈')
    // 前后文字仍然完整（没有把代理对切坏）：去掉编号后应逐字等于原文
    const rendered = screen.getByTestId('document-renderer').textContent ?? ''
    expect(rendered.replace('〔S2〕', '')).toBe(astralText)
  })

  it('候选覆盖用码点定位（跨 astral 字符也不会错位）', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[astralNode()]}
        candidates={[{ quoteId: 'q-cross', startCp: base + 1, endCp: base + 6 }]}
      />,
    )

    expect(screen.getByTestId('candidate-quote')).toHaveTextContent('野家的猫🐈')
  })
})
