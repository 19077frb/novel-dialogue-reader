import { render, screen, fireEvent } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { RosterRepairSettings } from '../src/components/RosterRepairSettings'
import { getDefaultProcessingPreferences, rosterRepairOptions, estimateRosterTokens } from '../src/processing/preferences'

it('keeps old snapshots off and estimates only opted-in extra calls', () => {
  const defaults = getDefaultProcessingPreferences()
  expect(rosterRepairOptions(defaults)).toEqual({})
  expect(rosterRepairOptions({ ...defaults, rosterRepairEnabled: undefined } as unknown as typeof defaults)).toEqual({})
  expect(estimateRosterTokens(100, defaults)).toBe(2100)
  const enabled = { ...defaults, rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 1 }
  expect(rosterRepairOptions(enabled)).toEqual({ rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 1 })
  expect(estimateRosterTokens(100, enabled)).toBe(8400)
})

it('explains locked controls and separates repair count from primary format retries', () => {
  const onChange = vi.fn()
  const preferences = { ...getDefaultProcessingPreferences(), rosterRepairEnabled: true }
  const view = render(<RosterRepairSettings experimentalEnabled preferences={preferences} onChange={onChange} disabled />)
  expect(screen.getByTestId('roster-repair-enabled')).toBeDisabled()
  expect(screen.getByText(/当前处理任务尚未结束/)).toBeVisible()
  expect(screen.getByLabelText(/人物证据最多修复次数/)).toBeDisabled()
  expect(screen.getByLabelText(/人物证据最多修复次数/)).toHaveAttribute('title', expect.stringMatching(/修复.*格式错误.*修复次数/))
  view.rerender(<RosterRepairSettings experimentalEnabled preferences={preferences} onChange={onChange} />)
  fireEvent.change(screen.getByLabelText(/人物证据最多修复次数/), { target: { value: '3' } })
  expect(onChange).toHaveBeenCalledWith({ maxRosterRepairs: 3 })
})
