import { render, screen, fireEvent } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { WindowPicker } from '../src/components/WindowPicker'

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
