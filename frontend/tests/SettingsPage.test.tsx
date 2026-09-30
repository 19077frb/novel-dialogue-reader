import { fireEvent, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import SettingsPage from '../src/pages/SettingsPage'
import { getGeneralSettings, SETTINGS_KEY } from '../src/settings/preferences'
import { renderWithProviders } from './helpers'

beforeEach(() => localStorage.clear())
afterEach(() => { localStorage.clear(); vi.restoreAllMocks() })
it('persists display preferences and rehydrates the same values on reopening', async () => {
  const first = renderWithProviders(<SettingsPage />)
  fireEvent.change(screen.getByLabelText('正文字号'), { target: { value: 22 } })
  fireEvent.change(screen.getByLabelText('正文行距'), { target: { value: 2.2 } })
  await userEvent.click(screen.getByLabelText(/默认显示候选引语/))
  expect(JSON.parse(localStorage.getItem(SETTINGS_KEY)!)).toMatchObject({ fontSize: 22, lineHeight: 2.2, showCandidates: false })
  first.unmount()
  renderWithProviders(<SettingsPage />)
  expect(screen.getByLabelText('正文字号')).toHaveValue(22)
  expect(screen.getByLabelText(/默认显示候选引语/)).not.toBeChecked()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  await userEvent.click(screen.getByRole('button', { name: '恢复默认设置' }))
  expect(screen.getByLabelText('正文字号')).toHaveValue(16)
})
it('bounds corrupt stored values and falls back safely for invalid JSON', () => {
  localStorage.setItem(SETTINGS_KEY, '{bad')
  expect(getGeneralSettings().fontSize).toBe(16)
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ fontSize: 1000, lineHeight: -1, showAnnotations: 'no' }))
  expect(getGeneralSettings()).toMatchObject({ fontSize: 28, lineHeight: 1.5, showAnnotations: true })
})
