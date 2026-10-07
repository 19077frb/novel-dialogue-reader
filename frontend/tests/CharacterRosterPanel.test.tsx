import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, it, vi } from 'vitest'
import * as charactersApi from '../src/api/characters'
import * as jobsApi from '../src/api/jobs'
import * as booksApi from '../src/api/books'
import type { ChapterRosterOut, JobDetailOut } from '../src/api/types'
import { CharacterRosterPanel } from '../src/components/CharacterRosterPanel'
import { updateProcessingPreferences } from '../src/processing/preferences'

vi.mock('../src/api/characters', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/characters')>(),
  fetchCharacterRoster: vi.fn(), fetchBookCharacters: vi.fn(),
  analyzeCharacterRoster: vi.fn(), confirmCharacterRoster: vi.fn(),
}))
vi.mock('../src/api/jobs', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/jobs')>(), fetchRecentJobs: vi.fn(),
}))
vi.mock('../src/api/books', async importOriginal => ({
  ...await importOriginal<typeof import('../src/api/books')>(), fetchJob: vi.fn(),
}))

function roster(chapter = 'c1', names = ['林舟', '陆欣', '陈阳', '女同学']): ChapterRosterOut {
  return { chapter_id: chapter, book_version_id: 'v1', version: 1, status: 'DRAFT',
    confirmed_characters: [], pov_character_id: null,
    candidates: names.map((name, index) => ({ temp_ref: `p${index}`, character_id: `person${index}`,
      canonical_name: name, aliases: [], description: '原文人物', evidence_refs: ['L1'],
      pov_candidate: index === 0 })) }
}
const journalKey = (chapter = 'c1') => `ndr:tasks:v1:roster-draft:b1:v1:${chapter}`
const props = { bookId: 'b1', bookVersionId: 'v1', chapterId: 'c1', profileId: 'profile',
  onConfirmedChange: vi.fn() }
function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const ui = (chapter = 'c1') => <QueryClientProvider client={client}><MemoryRouter>
    <CharacterRosterPanel {...props} chapterId={chapter} />
  </MemoryRouter></QueryClientProvider>
  return { client, ui, ...render(ui()) }
}
beforeEach(() => {
  localStorage.clear()
  vi.clearAllMocks()
  vi.mocked(charactersApi.fetchCharacterRoster).mockImplementation(async (_book, chapter) => roster(chapter))
  vi.mocked(charactersApi.fetchBookCharacters).mockResolvedValue([])
  vi.mocked(jobsApi.fetchRecentJobs).mockResolvedValue([])
})

it('submits shared repair limits and quota with a new single-chapter analysis', async () => {
  updateProcessingPreferences({ rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 3, tokenLimit: 50000 })
  const completed = { id: 'new-roster', state: 'COMPLETED' } as JobDetailOut
  vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(completed)
  vi.mocked(booksApi.fetchJob).mockResolvedValue(completed)
  mount()
  await waitFor(() => expect(screen.getByTestId('roster-analyze')).toBeEnabled())
  fireEvent.click(screen.getByTestId('roster-analyze'))
  await waitFor(() => expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledWith('b1', 'c1', expect.objectContaining({
    rosterRepairEnabled: true, maxRosterRepairs: 2, maxFormatRetries: 3, maxInputTokens: 50000,
  })))
})

it('shows all four saved candidates instead of a legacy empty version-1 draft without model calls', async () => {
  localStorage.setItem(journalKey(), JSON.stringify({ version: 1, drafts: [], pov: null }))
  mount()
  expect(await screen.findByTestId('roster-name-p0')).toHaveValue('林舟')
  expect(screen.getByTestId('roster-name-p3')).toHaveValue('女同学')
  expect(screen.queryByText('还没有人物候选。可以先分析，也可以直接手动添加。')).not.toBeInTheDocument()
  expect(screen.getByTestId('roster-confirm')).toBeEnabled()
  await waitFor(() => expect(JSON.parse(localStorage.getItem(journalKey())!).drafts).toHaveLength(4))
  expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
})

it('preserves same-source manual edits and added people after remount', async () => {
  const first = mount()
  await screen.findByTestId('roster-name-p0')
  fireEvent.change(screen.getByTestId('roster-name-p0'), { target: { value: '手动改名' } })
  fireEvent.click(screen.getByTestId('roster-add-character'))
  const input = screen.getAllByTestId(/^roster-name-user-/)[0]
  fireEvent.change(input, { target: { value: '手动新增' } })
  await waitFor(() => expect(JSON.parse(localStorage.getItem(journalKey())!).drafts).toHaveLength(5))
  first.unmount()
  mount()
  expect(await screen.findByTestId('roster-name-p0')).toHaveValue('手动改名')
  expect(screen.getAllByTestId(/^roster-name-user-/)[0]).toHaveValue('手动新增')
})

it('rejects an obsolete sourced draft when server candidates change even at the same legacy version', async () => {
  const view = mount()
  await screen.findByTestId('roster-name-p0')
  fireEvent.change(screen.getByTestId('roster-name-p0'), { target: { value: '旧分析编辑' } })
  const updated = roster('c1', ['新分析姓名'])
  view.client.setQueryData(charactersApi.characterKeys.roster('b1', 'v1', 'c1'), updated)
  await waitFor(() => expect(screen.getByTestId('roster-name-p0')).toHaveValue('新分析姓名'))
  expect(screen.queryByTestId('roster-name-p1')).not.toBeInTheDocument()
  await waitFor(() => expect(JSON.parse(localStorage.getItem(journalKey())!).source).toBe(JSON.stringify(updated)))
})

it('does not write previous chapter state over the next chapter same-version draft', async () => {
  const second = roster('c2', ['第二章人物'])
  localStorage.setItem(journalKey('c2'), JSON.stringify({ version: 1, source: JSON.stringify(second),
    pov: 'p0', drafts: [{ temp_ref: 'p0', character_id: 'person0', canonical_name: '第二章手动编辑',
      aliasesText: '', accepted: true, description: '原文人物', evidence_refs: ['L1'], pov_candidate: true }] }))
  const view = mount()
  await screen.findByTestId('roster-name-p0')
  fireEvent.change(screen.getByTestId('roster-name-p0'), { target: { value: '第一章编辑' } })
  view.client.setQueryData(charactersApi.characterKeys.roster('b1', 'v1', 'c2'), second)
  vi.mocked(charactersApi.fetchCharacterRoster).mockResolvedValue(second)
  view.rerender(view.ui('c2'))
  await waitFor(() => expect(screen.getByTestId('roster-name-p0')).toHaveValue('第二章手动编辑'))
  expect(JSON.parse(localStorage.getItem(journalKey('c1'))!).drafts[0].canonical_name).toBe('第一章编辑')
  expect(JSON.parse(localStorage.getItem(journalKey('c2'))!).drafts[0].canonical_name).toBe('第二章手动编辑')
})

it('keeps legacy nonempty manual drafts but does not restore them over confirmed data', async () => {
  const saved = { version: 1, pov: 'manual', drafts: [{ temp_ref: 'manual', character_id: null,
    canonical_name: '旧手动编辑', aliasesText: '', accepted: true, description: '',
    evidence_refs: [], pov_candidate: true }] }
  localStorage.setItem(journalKey(), JSON.stringify(saved))
  const view = mount()
  expect(await screen.findByTestId('roster-name-manual')).toHaveValue('旧手动编辑')
  view.client.setQueryData(charactersApi.characterKeys.roster('b1', 'v1', 'c1'),
    { ...roster(), status: 'CONFIRMED', pov_character_id: 'person0' })
  expect(await screen.findByTestId('roster-name-p0')).toHaveValue('林舟')
  expect(screen.queryByTestId('roster-name-manual')).not.toBeInTheDocument()
})

it('restores saved partial-proposal warnings without leaking them into a different chapter', async () => {
  const job = { id: 'roster-job', state: 'COMPLETED', range: { chapter_id: 'c1' },
    created_at: '2026-10-06T00:00:00Z', updated_at: '2026-10-06T00:00:01Z',
    progress: { proposal_diagnostics: { isolated_characters: 1, discarded_auxiliary_facts: 2,
      discarded_descriptions: 0, details: [{ character_index: 2, message: '姓名没有原文依据' }] } },
  } as unknown as JobDetailOut
  vi.mocked(booksApi.fetchJob).mockResolvedValue(job)
  vi.mocked(jobsApi.fetchRecentJobs).mockImplementation(async input => input.chapterId === 'c1' ? [job] : [])
  const view = mount()
  expect(await screen.findByText(/已保留有效人物提案/)).toHaveTextContent('隔离 1 个人物')
  expect(screen.getByTestId('roster-name-p0')).toHaveValue('林舟')
  fireEvent.click(screen.getByRole('button', { name: '展开人物分析提示' }))
  expect(screen.getByText('第 2 个人物：姓名没有原文依据')).toBeVisible()
  view.rerender(view.ui('c2'))
  await waitFor(() => expect(screen.queryByText(/已保留有效人物提案/)).not.toBeInTheDocument())
  expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
})
