import { beforeEach, expect, it, vi } from 'vitest'
import { apiData } from '../src/api/client'
import { analyzeCharacterRoster } from '../src/api/characters'

vi.mock('../src/api/client', () => ({ apiData: vi.fn() }))
beforeEach(() => { vi.clearAllMocks(); vi.mocked(apiData).mockResolvedValue({} as never) })

it('omits unused repair fields on legacy/restored requests', async () => {
  await analyzeCharacterRoster('b', 'c', { bookVersionId: 'v', profileId: 'p', idempotencyKey: 'old', maxRosterRepairs: 5 })
  const body = (vi.mocked(apiData).mock.calls[0][1] as { body: Record<string, unknown> }).body
  expect(body).not.toHaveProperty('roster_repair_enabled')
  expect(body).not.toHaveProperty('max_roster_repairs')
  expect(body).not.toHaveProperty('max_format_retries')
})

it('sends explicit bounded repair settings unchanged, including zero', async () => {
  await analyzeCharacterRoster('b', 'c', { bookVersionId: 'v', profileId: 'p', idempotencyKey: 'new',
    rosterRepairEnabled: true, maxRosterRepairs: 0, maxFormatRetries: 3, maxInputTokens: 10000 })
  expect(apiData).toHaveBeenCalledWith('/api/books/b/chapters/c/character-roster/analyze', expect.objectContaining({
    body: expect.objectContaining({ roster_repair_enabled: true, max_roster_repairs: 0, max_format_retries: 3, max_input_tokens: 10000 }),
  }))
})
