import { render, screen, fireEvent } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useState } from 'react'
import { WindowPicker } from '../src/components/WindowPicker'

it('keeps window selections across pages and selects all windows, not just the visible page', () => {
  const windows = Array.from({ length: 23 }, (_, i) => ({ window_id: `w${i + 1}`, ordinal: i + 1, target_count: 2 }))
  function Example() {
    const [selected, setSelected] = useState<string[]>([])
    return <WindowPicker windows={windows} selectedIds={selected} onChange={setSelected} />
  }
  render(<Example />)
  expect(screen.getByTestId('window-w21')).not.toBeVisible()
  fireEvent.click(screen.getByTestId('window-w1'))
  fireEvent.click(screen.getByRole('button', { name: '下一页' }))
  fireEvent.click(screen.getByTestId('window-w21'))
  expect(screen.getByTestId('windows-selection-summary')).toHaveTextContent('已选 2/23')
  fireEvent.click(screen.getByRole('button', { name: '上一页' }))
  expect(screen.getByTestId('window-w1')).toBeChecked()
  fireEvent.click(screen.getByTestId('windows-select-all'))
  expect(screen.getByTestId('windows-selection-summary')).toHaveTextContent('已选 23/23')
  expect(screen.getByTestId('window-w23')).toBeChecked()
})

it('shows saved statuses and selects only failed or incomplete windows', () => {
  const onChange = vi.fn()
  render(<WindowPicker windows={[
    { window_id: 'w1', ordinal: 1, processing_status: 'completed', processed_target_count: 2, target_count: 2 },
    { window_id: 'w2', ordinal: 2, processing_status: 'failed', last_error: '模型失败', target_count: 2 },
    { window_id: 'w3', ordinal: 3, processing_status: 'unprocessed', target_count: 2 },
  ]} selectedIds={['w2', 'w3']} onChange={onChange} />)
  expect(screen.getByText(/已完成.*已保存 2\/2/)).toBeInTheDocument()
  expect(screen.getByText(/失败.*模型失败/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '只选失败窗口' }))
  expect(onChange).toHaveBeenLastCalledWith(['w2'])
  fireEvent.click(screen.getByRole('button', { name: '只选未完成窗口' }))
  expect(onChange).toHaveBeenLastCalledWith(['w2', 'w3'])
})

it('can collapse during processing without changing selections or enabling disabled inputs', () => {
  const onChange = vi.fn()
  render(<WindowPicker windows={[{ window_id: 'w1', ordinal: 1, target_count: 2 }]}
    selectedIds={['w1']} onChange={onChange} disabled />)
  const input = screen.getByTestId('window-w1')
  expect(input).toBeChecked()
  expect(input).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '收起窗口列表' }))
  expect(input).not.toBeVisible()
  expect(screen.getByText(/共 1 个窗口；已选 1 个/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '展开窗口列表' }))
  expect(input).toBeChecked()
  expect(input).toBeDisabled()
  expect(onChange).not.toHaveBeenCalled()
})
