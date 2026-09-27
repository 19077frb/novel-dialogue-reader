import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { ContentNodeOut } from '../src/api/types'
import { DocumentRenderer } from '../src/components/DocumentRenderer'

function node(partial: Partial<ContentNodeOut> & Pick<ContentNodeOut, 'node_id' | 'node_type'>): ContentNodeOut {
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
    const paragraph = screen.getByText('「你好。」')
    expect(paragraph).toBeInTheDocument()

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

    const rt = document.querySelector('rt')
    expect(rt?.textContent).toBe('かん')
    const ruby = document.querySelector('ruby')
    expect(ruby?.textContent?.startsWith('漢')).toBe(true)
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

  it('没有标注时不渲染任何着色或编号（AnnotationLayer 只是占位）', () => {
    render(
      <DocumentRenderer
        bookId="b1"
        nodes={[node({ node_id: 'n0', node_type: 'paragraph', start_cp: 0, end_cp: 3, text: '正文。' })]}
      />,
    )

    const layer = screen.getByTestId('annotation-layer')
    expect(layer).toHaveAttribute('data-annotation-count', '0')
    // 没有识别结果：不能出现人物编号或颜色类名。
    expect(layer.querySelectorAll('[class*="speaker"]')).toHaveLength(0)
    expect(layer.querySelectorAll('[class*="quote"]')).toHaveLength(0)
  })
})