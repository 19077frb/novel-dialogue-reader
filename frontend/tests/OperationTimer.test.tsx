import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { OperationTimer, durationLabel } from '../src/components/OperationTimer'

afterEach(() => vi.useRealTimers())

it('ticks locally, estimates from measured progress and freezes on completion', () => {
  vi.useFakeTimers()
  vi.setSystemTime(100_000)
  const view = render(<OperationTimer startedAt={90_000} completed={2} total={4} />)
  expect(screen.getByTestId('operation-timer')).toHaveTextContent('已等待 10 秒 · 预计还需约 10 秒')
  act(() => vi.advanceTimersByTime(2000))
  expect(screen.getByTestId('operation-timer')).toHaveTextContent('已等待 12 秒')
  view.rerender(<OperationTimer startedAt={90_000} finishedAt={102_000} />)
  act(() => vi.advanceTimersByTime(3000))
  expect(screen.getByTestId('operation-timer')).toHaveTextContent('用时 12 秒')
  expect(vi.getTimerCount()).toBe(0)
})

it('does not invent an estimate without progress or render invalid dates', () => {
  vi.useFakeTimers(); vi.setSystemTime(100_000)
  const view = render(<OperationTimer startedAt={99_000} />)
  expect(screen.getByTestId('operation-timer')).toHaveTextContent('暂无足够进度')
  view.rerender(<OperationTimer startedAt={NaN} />)
  expect(screen.queryByTestId('operation-timer')).not.toBeInTheDocument()
  expect(durationLabel(61)).toBe('1 分 1 秒')
})
