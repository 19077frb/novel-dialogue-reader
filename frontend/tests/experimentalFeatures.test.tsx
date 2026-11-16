import { act, fireEvent, renderHook, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { getGeneralSettings, updateGeneralSettings, SETTINGS_KEY } from '../src/settings/preferences'
import { getStoredProcessingPreferences, updateProcessingPreferences, useProcessingPreferences } from '../src/processing/preferences'
import { DialogueStrategySettings } from '../src/components/DialogueStrategySettings'
import { RosterRepairSettings } from '../src/components/RosterRepairSettings'

beforeEach(() => localStorage.clear())

it('requires a real boolean opt-in and keeps experimental controls absent by default', () => {
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: 'true' }))
  expect(getGeneralSettings().enableExperimentalFeatures).toBe(false)
  render(<><DialogueStrategySettings value="legacy" onChange={() => {}} rounds={1} />
    <RosterRepairSettings preferences={getStoredProcessingPreferences()} onChange={() => {}} /></>)
  expect(screen.queryByTestId('dialogue-strategy')).not.toBeInTheDocument()
  expect(screen.queryByTestId('roster-repair-enabled')).not.toBeInTheDocument()
})

it('reacts to saved and cross-tab general settings without losing remembered experimental choices', () => {
  updateProcessingPreferences({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  const { result } = renderHook(() => useProcessingPreferences()[0])
  expect(result.current.dialogueStrategy).toBe('legacy')
  act(() => updateGeneralSettings({ enableExperimentalFeatures: true, enableDialogueStrategies: true }))
  expect(result.current).toMatchObject({ dialogueStrategy: 'complete-review', rosterRepairEnabled: true })
  act(() => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ enableExperimentalFeatures: false }))
    window.dispatchEvent(new StorageEvent('storage', { key: SETTINGS_KEY }))
  })
  expect(result.current).toMatchObject({ dialogueStrategy: 'legacy', rosterRepairEnabled: false })
  expect(getStoredProcessingPreferences().rosterRepairEnabled).toBe(true)
})

it.each([
  [false, false, false], [false, true, false], [true, false, false], [true, true, true],
])('shows strategy controls for master %s and strategy %s only when both are enabled', (master, strategy, visible) => {
  updateGeneralSettings({ enableExperimentalFeatures: master, enableDialogueStrategies: strategy })
  render(<DialogueStrategySettings value="legacy" onChange={() => {}} rounds={1} />)
  if (visible) expect(screen.getByTestId('dialogue-strategy')).toBeVisible()
  else expect(screen.queryByTestId('dialogue-strategy')).not.toBeInTheDocument()
})

it('uses the explicit draft gate independently from saved experimental switches', () => {
  const view = render(<DialogueStrategySettings value="legacy" onChange={() => {}} rounds={1} experimentalEnabled />)
  expect(screen.getByTestId('dialogue-strategy')).toBeVisible()
  act(() => updateGeneralSettings({ enableExperimentalFeatures: true, enableDialogueStrategies: true }))
  view.rerender(<DialogueStrategySettings value="legacy" onChange={() => {}} rounds={1} experimentalEnabled={false} />)
  expect(screen.queryByTestId('dialogue-strategy')).not.toBeInTheDocument()
})

it('keeps costs and strategy prerequisites visible in the compact strategy editor', () => {
  const change = vi.fn()
  render(<DialogueStrategySettings value="complete-blocks-isolated-feedback-review" onChange={change} rounds={0} experimentalEnabled compact />)
  expect(screen.getByRole('region', { name: '对白处理策略' })).toHaveClass('ndr-experiment-details')
  expect(screen.getByText(/人物名单反馈需要独立复核/)).toBeVisible()
  expect(screen.getByText(/复核次数越多、思考越深入/)).toBeVisible()
  expect(screen.getByText(/每窗额外调用一次模型，检查是否漏掉人物或认错身份/)).toBeVisible()
  expect(screen.getByText(/拿不准时，选带“推荐”标签/)).not.toBeVisible()
  fireEvent.change(screen.getByTestId('dialogue-strategy'), { target: { value: 'complete-blocks' } })
  expect(change).toHaveBeenCalledWith('complete-blocks')
})

it('keeps the compact roster switch and repair count independent from shared format retries', () => {
  const change = vi.fn()
  const preferences = { ...getStoredProcessingPreferences(), rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 4 }
  render(<RosterRepairSettings preferences={preferences} onChange={change} experimentalEnabled compact />)
  expect(screen.getByRole('region', { name: '人物证据修复' })).toHaveClass('ndr-experiment-details')
  expect(screen.getByLabelText('人物证据定向修复')).toBeChecked()
  expect(screen.getByLabelText('人物证据最多修复次数（0 关闭修复）')).toHaveValue(2)
  expect(screen.queryByLabelText(/校验失败后的重试次数/)).not.toBeInTheDocument()
  expect(screen.getByText(/额外调用会增加用量与等待/)).toBeVisible()
  fireEvent.change(screen.getByLabelText('人物证据最多修复次数（0 关闭修复）'), { target: { value: '3' } })
  expect(change).toHaveBeenCalledWith({ maxRosterRepairs: 3 })
  expect(preferences.maxFormatRetries).toBe(4)
})
