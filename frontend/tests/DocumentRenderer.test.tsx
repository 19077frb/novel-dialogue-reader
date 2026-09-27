import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ContentNodeOut } from '../src/api/types'
import { DocumentRenderer } from '../src/components/DocumentRenderer'

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