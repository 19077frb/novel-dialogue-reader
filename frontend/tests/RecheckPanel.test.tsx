import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, expect, it, vi } from 'vitest'
import * as profilesApi from '../src/api/profiles'
import * as reviewApi from '../src/api/review'
import * as jobsApi from '../src/api/jobs'
import { RecheckPanel } from '../src/components/RecheckPanel'
import { updateProcessingPreferences } from '../src/processing/preferences'
import type { JobDetailOut, ModelProfileOut } from '../src/api/types'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/profiles', () => ({ profileKeys: { profiles: () => ['profiles'] }, fetchProfiles: vi.fn() }))
vi.mock('../src/api/review', () => ({ recheckQuote: vi.fn() }))
vi.mock('../src/api/jobs', async importOriginal => ({ ...await importOriginal<typeof import('../src/api/jobs')>(), fetchRecentJobs: vi.fn() }))
vi.mock('../src/components/JobPanel', () => ({ JobPanel: ({ onUpdate }: { onUpdate: (job: JobDetailOut) => void }) =>
  <button onClick={() => onUpdate({ id: 'job', state: 'COMPLETED' } as JobDetailOut)}>模拟任务完成</button> }))
const profile: ModelProfileOut = {
  id: 'p1', name: '测试配置', model: 'test', protocol: 'fake-provider',
  base_url: 'http://127.0.0.1:1', credential_mode: 'none', has_key: false,
  version: 1, created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  params: { thinking: { type: 'adaptive' }, reasoning_effort: 'high' },
}
beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  vi.mocked(jobsApi.fetchRecentJobs).mockResolvedValue([])
  vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([profile])
  vi.mocked(reviewApi.recheckQuote).mockResolvedValue({ id: 'job', state: 'QUEUED' } as JobDetailOut)
})

it('finds an existing recheck after remount without creating a new model request', async () => {
  vi.mocked(jobsApi.fetchRecentJobs).mockResolvedValue([{ id: 'saved-job', state: 'RUNNING' } as JobDetailOut])
  renderWithProviders(<RecheckPanel quoteId="q1" />)
  await waitFor(() => expect(jobsApi.fetchRecentJobs).toHaveBeenCalledWith({ quoteId: 'q1', kind: 'RECHECK', limit: 1 }, expect.any(AbortSignal)))
  expect(screen.getByTestId('recheck-start')).toBeDisabled()
  expect(reviewApi.recheckQuote).not.toHaveBeenCalled()
})

it('inherits shared thinking preferences, sends overrides and locks the running task', async () => {
  updateProcessingPreferences({ profileId: 'p1', thinkingMode: 'enabled', thinkingEffort: 'low', maxFormatRetries: 3 })
  const mounted = renderWithProviders(<RecheckPanel quoteId="q1" />)
  expect(await screen.findByTestId('profile-thinking-defaults')).toHaveTextContent('模式 自适应；强度 高')
  const settings = screen.getByTestId('model-thinking-settings')
  expect(within(settings).getByTestId('recheck-profile')).toHaveValue('p1')
  expect(within(settings).getByTestId('processing-thinking-mode')).toHaveValue('enabled')
  expect(screen.getByTestId('processing-thinking-mode')).toHaveValue('enabled')
  expect(screen.getByTestId('processing-thinking-effort')).toHaveValue('low')
  await userEvent.click(screen.getByTestId('recheck-start'))
  await waitFor(() => expect(reviewApi.recheckQuote).toHaveBeenCalled())
  expect(reviewApi.recheckQuote).toHaveBeenCalledWith('q1', expect.objectContaining({
    profileId: 'p1', inferenceOptions: { thinking_mode: 'enabled', reasoning_effort: 'low' }, maxInputTokens: 20000, maxFormatRetries: 3,
  }))
  expect(screen.getByTestId('processing-thinking-mode')).toBeDisabled()
  expect(screen.getByTestId('recheck-start')).toBeDisabled()
  expect(screen.getByTestId('recheck-format-retries')).toBeDisabled()
  await userEvent.click(screen.getByText('模拟任务完成'))
  await userEvent.selectOptions(screen.getByTestId('processing-thinking-effort'), 'high')
  await userEvent.click(screen.getByTestId('recheck-start'))
  await waitFor(() => expect(reviewApi.recheckQuote).toHaveBeenCalledTimes(2))
  const calls = vi.mocked(reviewApi.recheckQuote).mock.calls
  expect(calls[1][1].idempotencyKey).not.toBe(calls[0][1].idempotencyKey)
  mounted.unmount()
  renderWithProviders(<RecheckPanel quoteId="q1" />)
  expect(screen.getByTestId('processing-thinking-effort')).toHaveValue('high')
})

it('uses profile defaults when inherited and validates the explicit input limit', async () => {
  vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([{ ...profile, params: { thinking: { type: 'disabled' } } }])
  renderWithProviders(<RecheckPanel quoteId="q1" />)
  await screen.findByTestId('profile-thinking-defaults')
  expect(screen.getByTestId('processing-thinking-effort')).toBeDisabled()
  await userEvent.clear(screen.getByTestId('recheck-max-input'))
  await userEvent.type(screen.getByTestId('recheck-max-input'), '0')
  await userEvent.click(screen.getByTestId('recheck-start'))
  expect(screen.getByTestId('recheck-error')).toHaveTextContent('正整数')
  expect(reviewApi.recheckQuote).not.toHaveBeenCalled()
  await userEvent.clear(screen.getByTestId('recheck-max-input'))
  await userEvent.click(screen.getByTestId('recheck-start'))
  await waitFor(() => expect(reviewApi.recheckQuote).toHaveBeenCalled())
  expect(reviewApi.recheckQuote).toHaveBeenCalledWith('q1', expect.objectContaining({ inferenceOptions: undefined, maxInputTokens: null }))
})
