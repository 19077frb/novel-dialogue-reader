import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it } from 'vitest'
import { DisabledHint, disabledHint } from '../src/components/DisabledHint'
import { ThinkingSettings } from '../src/components/ThinkingSettings'
import { updateProcessingPreferences } from '../src/processing/preferences'

beforeEach(() => {
  updateProcessingPreferences({ thinkingMode: 'default', thinkingEffort: 'default' })
})

it('only displays a real blocker and exposes the same hover and accessible explanation', () => {
  const view = render(<><DisabledHint reason="请先选择模型配置。" />
    <button disabled {...disabledHint('请先选择模型配置。')}>开始处理</button></>)
  expect(screen.getByRole('status')).toHaveTextContent('请先选择模型配置。')
  const button = screen.getByRole('button', { name: '开始处理' })
  expect(button).toBeDisabled()
  expect(button).toHaveAttribute('title', '请先选择模型配置。')
  expect(button).toHaveAttribute('aria-description', '请先选择模型配置。')
  view.rerender(<DisabledHint reason={null} />)
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it('uses the actual parent lock reason rather than claiming a running task', () => {
  render(<ThinkingSettings disabled disabledReason="任务状态读取失败，请先重新读取。"
    profiles={[]} profileId="" onProfileChange={() => {}} profileTestId="locked-profile" />)
  expect(screen.getByRole('status')).toHaveTextContent('任务状态读取失败，请先重新读取。')
  expect(screen.getByTestId('locked-profile')).toHaveAttribute('title', '任务状态读取失败，请先重新读取。')
  expect(screen.getByTestId('processing-thinking-effort')).toBeDisabled()
})

it('explains disabled effort and clears the hint when thinking is enabled', async () => {
  updateProcessingPreferences({ thinkingMode: 'disabled' })
  render(<ThinkingSettings disabled={false} profiles={[]} profileId=""
    onProfileChange={() => {}} profileTestId="unlocked-profile" />)
  expect(screen.getByRole('status')).toHaveTextContent('思考已关闭，无法调整思考强度')
  const effort = screen.getByTestId('processing-thinking-effort')
  expect(effort).toBeDisabled()
  await userEvent.selectOptions(screen.getByTestId('processing-thinking-mode'), 'enabled')
  expect(effort).toBeEnabled()
  expect(effort).not.toHaveAttribute('title')
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})
