import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as annotationsApi from '../src/api/annotations'
import * as booksApi from '../src/api/books'
import * as charactersApi from '../src/api/characters'
import * as jobsApi from '../src/api/jobs'
import * as profilesApi from '../src/api/profiles'
import type {
  AnnotationsResponse,
  BookOut,
  ChapterOut,
  ChapterRosterOut,
  ContentNodeOut,
  EstimateOut,
  JobDetailOut,
  UsageOut,
  QuoteNormalizationOut,
  QuoteNormalizationRefreshOut,
} from '../src/api/types'
import PreviewPage from '../src/pages/PreviewPage'
import PreprocessingPage from '../src/pages/PreprocessingPage'
import { renderRoute } from './helpers'
import { updateGeneralSettings } from '../src/settings/preferences'

vi.mock('../src/api/books', () => ({
  queryKeys: {
    book: (id: string) => ['book', id],
    chapters: (id: string) => ['chapters', id],
    content: (id: string, chapter: string | null, cursor: string | null) => [
      'content',
      id,
      chapter,
      cursor,
    ],
    quotes: (id: string, chapter: string | null) => ['quotes', id, chapter],
    quoteNormalizations: (id: string) => ['quote-normalizations', id],
    job: (id: string) => ['job', id],
  },
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchContent: vi.fn(),
  fetchQuotes: vi.fn(),
  fetchJob: vi.fn(),
  completeChapterProcessing: vi.fn(),
  fetchQuoteNormalizations: vi.fn(),
  refreshQuoteNormalizations: vi.fn(),
  updateQuoteNormalization: vi.fn(),
}))

vi.mock('../src/api/characters', () => ({
  characterKeys: {
    book: (bookId: string, versionId: string | null | undefined) => [
      'book-characters',
      bookId,
      versionId ?? 'active',
    ],
    roster: (
      bookId: string,
      versionId: string | null | undefined,
      chapterId: string | null,
    ) => ['character-roster', bookId, versionId ?? 'active', chapterId],
    rosterJob: (jobId: string | null) => ['character-roster-job', jobId],
  },
  fetchBookCharacters: vi.fn(),
  fetchCharacterRoster: vi.fn(),
  analyzeCharacterRoster: vi.fn(),
  confirmCharacterRoster: vi.fn(),
}))

vi.mock('../src/api/annotations', () => ({
  annotationKeys: {
    range: (
      bookId: string,
      startCp: number,
      endCp: number,
      mode: string,
      horizon: number | null,
    ) => ['annotations', bookId, startCp, endCp, mode, horizon],
  },
  fetchAnnotations: vi.fn(),
}))

vi.mock('../src/api/jobs', () => ({
  jobKeys: { usage: (bookId: string) => ['usage', bookId] },
  estimateRange: vi.fn(),
  createJob: vi.fn(),
  fetchUsage: vi.fn(),
  fetchRecentJobs: vi.fn().mockResolvedValue([]),
  shortHash: (value: string) => String(value.length),
  freshIdempotencyKey: (scope: string, value: string) =>
    `${scope}:${value.length}:test-nonce`,
}))

vi.mock('../src/api/profiles', () => ({
  profileKeys: { profiles: () => ['model-profiles'], protocols: () => ['protocols'] },
  fetchProfiles: vi.fn(),
}))

const BOOK = {
  id: 'b1',
  title: '雨夜',
  format: 'TXT',
  source_sha256: 'a'.repeat(64),
  import_status: 'COMPLETED',
  read_position_cp: 0,
  reading_mode: 'initial',
  version: 1,
  active_version_id: 'v1',
  active_version: {
    id: 'v1',
    encoding: 'utf-8',
    parser_version: 'txt-1',
    normalization_version: 'canonical-lf-1',
    canonical_sha256: 'b'.repeat(64),
    canonical_length_cp: 40,
    warnings: [],
    created_at: '2026-09-28T00:00:00+00:00',
  },
  created_at: '2026-09-28T00:00:00+00:00',
  updated_at: '2026-09-28T00:00:00+00:00',
} as BookOut

const CHAPTERS = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 20, source_href: null },
] as ChapterOut[]

const NODES = [
  {
    node_id: 'n0',
    node_type: 'paragraph',
    ordinal: 0,
    start_cp: 0,
    end_cp: 14,
    chapter_id: 'c1',
    chapter_ordinal: 0,
    text: '「雨停了。」少女说。',
    payload: {},
  },
] as ContentNodeOut[]

const ANNOTATIONS = {
  identity_reverts: 0,
  book_id: 'b1',
  book_version_id: 'v1',
  reading_mode: 'initial',
  visible_horizon_cp: 20,
  start_cp: 0,
  end_cp: 20,
  items: [
    {
      quote_id: 'q1',
      scene_id: 's1',
      start_cp: 0,
      end_cp: 6,
      kind: 'speech',
      assignment: 'EXISTING',
      basis: 'DIRECT',
      status: 'ACCEPTED',
      source: 'MODEL',
      speaker_group_id: 'g1',
      label: 'S1',
      color_index: 0,
      stale: false,
      user_locked: false,
      withheld: false,
    },
  ],
  legend: [
    {
      group_id: 'g1',
      label: 'S1',
      scene_id: 's1',
      color_index: 0,
      first_quote_id: 'q1',
      description: '',
      quote_count: 1,
    },
  ],
  counts: {
    total: 1,
    accepted: 1,
    provisional: 0,
    unknown: 0,
    stale: 0,
    withheld: 0,
    unprocessed_quotes: 0,
  },
  scenes: [],
} as unknown as AnnotationsResponse

const ROSTER = {
  chapter_id: 'c1',
  book_version_id: 'v1',
  status: 'CONFIRMED',
  candidates: [
    {
      temp_ref: 'c1',
      character_id: 'char1',
      canonical_name: 'Speaker',
      aliases: [],
      description: '',
      evidence_refs: ['q1'],
      pov_candidate: true,
    },
  ],
  confirmed_characters: [
    {
      character_id: 'char1',
      name: 'Speaker',
      name_locked: false,
      confirmation_source: 'manual',
      aliases: [],
      description: '',
      user_confirmed: true,
    },
  ],
  pov_character_id: 'char1',
  version: 2,
} as ChapterRosterOut

const ESTIMATE = {
  book_id: 'b1',
  book_version_id: 'v1',
  window_count: 2,
  target_count: 3,
  input_tokens: 1200,
  output_tokens: 60,
  total_tokens: 1260,
  estimator: {},
  policy: {},
  notes: ['本地启发式'],
  windows: [
    {
      window_id: 'w1',
      ordinal: 1,
      start_cp: 0,
      end_cp: 8,
      target_count: 1,
      estimated_tokens: 620,
      preview: '「雨停了。」',
    },
    {
      window_id: 'w2',
      ordinal: 2,
      start_cp: 8,
      end_cp: 20,
      target_count: 2,
      estimated_tokens: 640,
      preview: '少女说。',
    },
  ],
} as EstimateOut

const JOB = {
  id: 'j1',
  kind: 'INFERENCE',
  purpose: 'preview',
  state: 'COMPLETED',
  book_id: 'b1',
  book_version_id: 'v1',
  progress: null,
  checkpoint: null,
  last_error: null,
  windows: [],
  remaining_windows: 0,
  windows_total: 2,
  calls: 2,
  cached_windows: 0,
  unknown_usage_runs: 1,
  usage: { input_tokens: 30, output_tokens: 10, unknown_runs: 1 },
  created_at: '2026-09-28T00:00:00+00:00',
  updated_at: '2026-09-28T00:00:00+00:00',
} as JobDetailOut

const USAGE = {
  book_id: 'b1',
  runs: 2,
  unknown_usage_runs: 1,
  input_tokens: 0,
  output_tokens: 0,
  total_tokens: 0,
  by_state: { SUCCEEDED: 2 },
  by_model: {},
} as UsageOut

describe('PreviewPage', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchContent).mockReset()
    vi.mocked(booksApi.fetchQuotes).mockReset()
    vi.mocked(booksApi.fetchJob).mockReset()
    vi.mocked(booksApi.completeChapterProcessing).mockReset()
    vi.mocked(booksApi.fetchQuoteNormalizations).mockReset()
    vi.mocked(booksApi.refreshQuoteNormalizations).mockReset()
    vi.mocked(booksApi.updateQuoteNormalization).mockReset()
    vi.mocked(booksApi.fetchQuoteNormalizations).mockResolvedValue([])
    vi.mocked(annotationsApi.fetchAnnotations).mockReset()
    vi.mocked(charactersApi.fetchBookCharacters).mockReset()
    vi.mocked(charactersApi.fetchCharacterRoster).mockReset()
    vi.mocked(charactersApi.analyzeCharacterRoster).mockReset()
    vi.mocked(charactersApi.confirmCharacterRoster).mockReset()
    vi.mocked(jobsApi.estimateRange).mockReset()
    vi.mocked(jobsApi.createJob).mockReset()
    vi.mocked(jobsApi.fetchUsage).mockReset()
    vi.mocked(profilesApi.fetchProfiles).mockReset()

    vi.mocked(booksApi.fetchBook).mockResolvedValue(BOOK)
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(CHAPTERS)
    vi.mocked(booksApi.fetchContent).mockResolvedValue({
      book_id: 'b1',
      book_version_id: 'v1',
      canonical_length_cp: 40,
      chapter_id: 'c1',
      start_cp: 0,
      end_cp: 20,
      nodes: NODES,
      next_cursor: null,
    })
    vi.mocked(booksApi.fetchQuotes).mockResolvedValue({
      items: [
        {
          quote_id: 'q1',
          book_version_id: 'v1',
          chapter_id: 'c1',
          chapter_ordinal: 0,
          start_cp: 0,
          end_cp: 6,
          text: '雨停了。',
          delimited_text: '「雨停了。」',
          delimiter: 'corner_bracket',
          opening: '「',
          closing: '」',
          nesting_depth: 0,
          parent_quote_id: null,
          kind_hint: null,
          scanner_version: 'quote-scan-1',
        normalized: false,
        },
      ],
      next_cursor: null,
    })
    vi.mocked(booksApi.fetchJob).mockResolvedValue(JOB)
    vi.mocked(booksApi.completeChapterProcessing).mockResolvedValue({
      chapter_id: 'c1',
      dialogue_processed: true,
      quote_count: 1,
      annotated_quote_count: 1,
    })
    vi.mocked(annotationsApi.fetchAnnotations).mockResolvedValue(ANNOTATIONS)
    vi.mocked(charactersApi.fetchBookCharacters).mockResolvedValue(ROSTER.confirmed_characters ?? [])
    vi.mocked(charactersApi.fetchCharacterRoster).mockResolvedValue(ROSTER)
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(JOB)
    vi.mocked(charactersApi.confirmCharacterRoster).mockResolvedValue(ROSTER)
    vi.mocked(jobsApi.estimateRange).mockResolvedValue(ESTIMATE)
    vi.mocked(jobsApi.createJob).mockResolvedValue(JOB)
    vi.mocked(jobsApi.fetchUsage).mockResolvedValue(USAGE)
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([
      {
        id: 'p1',
        name: '测试提供方',
        protocol: 'fake-provider',
        base_url: 'http://127.0.0.1:1',
        model: 'fake-model',
        credential_mode: 'none',
        has_key: false,
        version: 1,
        created_at: '2026-09-28T00:00:00+00:00',
        updated_at: '2026-09-28T00:00:00+00:00',
      },
    ] as never)
  })

  it('手动人物确认后重新读取与再次进入仍显示姓名说明和主人公', async () => {
    let current = ROSTER
    vi.mocked(charactersApi.fetchCharacterRoster).mockImplementation(async () => current)
    vi.mocked(charactersApi.confirmCharacterRoster).mockImplementation(async (_book, _chapter, input) => {
      const candidates = input.candidates.filter(item => item.accepted).map((item, index) => ({
        temp_ref: item.temp_ref,
        character_id: item.character_id ?? `manual-char-${index}`,
        canonical_name: item.canonical_name,
        aliases: item.aliases ?? [],
        description: item.description ?? '',
        evidence_refs: [],
        pov_candidate: item.temp_ref === input.povTempRef,
      }))
      current = { ...current, version: current.version + 1, candidates,
        pov_character_id: candidates.find(item => item.pov_candidate)!.character_id }
      return current
    })
    const mounted = renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('roster-name-c1')
    await userEvent.click(screen.getByTestId('roster-add-character'))
    const card = screen.getByTestId('roster-candidates').querySelectorAll('article')[1]
    await userEvent.type(within(card as HTMLElement).getByRole('textbox', { name: '姓名' }), '手动人物')
    await userEvent.type(within(card as HTMLElement).getByRole('textbox', { name: '说明' }), '手动说明')
    await userEvent.click(within(card as HTMLElement).getByRole('radio'))
    await userEvent.click(screen.getByTestId('roster-confirm'))
    await waitFor(() => expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(charactersApi.fetchCharacterRoster).toHaveBeenCalledTimes(2))
    const manualRef = current.candidates![1].temp_ref
    await waitFor(() => expect(screen.getByTestId(`roster-name-${manualRef}`)).toHaveValue('手动人物'))
    mounted.unmount()
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    const restored = (await screen.findByTestId(`roster-name-${manualRef}`)).closest('article')!
    expect(within(restored).getByRole('textbox', { name: '说明' })).toHaveValue('手动说明')
    expect(within(restored).getByRole('radio')).toBeChecked()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
  })

  it('显示当前模型的思考默认值，任务可覆盖默认关闭模式', async () => {
    const profiles = await profilesApi.fetchProfiles()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([{ ...profiles[0], params: { thinking: { type: 'disabled' } } }])
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    expect(await screen.findByTestId('profile-thinking-defaults')).toHaveTextContent('模式 关闭')
    expect(screen.getByTestId('processing-thinking-effort')).toBeDisabled()
    await userEvent.selectOptions(screen.getByTestId('processing-thinking-mode'), 'enabled')
    expect(screen.getByTestId('processing-thinking-effort')).not.toBeDisabled()
    await userEvent.selectOptions(screen.getByTestId('processing-thinking-effort'), 'high')
    await screen.findByTestId('window-picker')
    await userEvent.click(screen.getByTestId('preview-run'))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalled())
    expect(jobsApi.createJob).toHaveBeenCalledWith(expect.objectContaining({ inferenceOptions: { thinking_mode: 'enabled', reasoning_effort: 'high' } }))
  })

  it('单章与批量共享配置，重新进入页面仍保留配置', async () => {
    const profiles = await profilesApi.fetchProfiles()
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([...profiles, { ...profiles[0], id: 'p2', name: '第二模型' }])
    const mounted = renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByText('第二模型 · fake-provider · fake-model')
    const modelSettings = screen.getByTestId('model-thinking-settings')
    expect(within(modelSettings).getByTestId('preview-profile')).toBeInTheDocument()
    expect(within(modelSettings).getByTestId('processing-thinking-mode')).toBeInTheDocument()
    expect(modelSettings.compareDocumentPosition(screen.getByTestId('processing-mode-picker')) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    expect(modelSettings.compareDocumentPosition(screen.getByRole('heading', { name: '选择处理范围' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.change(screen.getByTestId('preview-profile'), { target: { value: 'p2' } })
    fireEvent.change(screen.getByTestId('budget-max-rechecks'), { target: { value: '7' } })
    fireEvent.change(screen.getByTestId('budget-format-retries'), { target: { value: '2' } })
    fireEvent.change(screen.getByTestId('preview-concurrency'), { target: { value: '4' } })
    fireEvent.change(screen.getByTestId('budget-max-input'), { target: { value: '50000' } })
    fireEvent.change(screen.getByTestId('processing-thinking-mode'), { target: { value: 'adaptive' } })
    fireEvent.change(screen.getByTestId('processing-thinking-effort'), { target: { value: 'high' } })
    await userEvent.click(screen.getByTestId('processing-mode-batch'))
    expect(screen.getByTestId('batch-profile')).toHaveValue('p2')
    expect(screen.getAllByTestId('model-thinking-settings')).toHaveLength(1)
    expect(within(modelSettings).getByTestId('batch-profile')).toBeInTheDocument()
    expect(within(screen.getByTestId('batch-processor')).queryByTestId('batch-profile')).not.toBeInTheDocument()
    expect(screen.getByTestId('batch-max-rechecks')).toHaveValue(7)
    expect(screen.getByTestId('batch-format-retries')).toHaveValue(2)
    expect(screen.getByTestId('batch-concurrency')).toHaveValue(4)
    expect(screen.getByTestId('batch-token-limit')).toHaveValue(50000)
    expect(screen.getByTestId('processing-thinking-mode')).toHaveValue('adaptive')
    fireEvent.change(screen.getByTestId('batch-max-rechecks'), { target: { value: '3' } })
    fireEvent.change(screen.getByTestId('batch-format-retries'), { target: { value: '4' } })
    fireEvent.change(screen.getByTestId('batch-token-limit'), { target: { value: '' } })
    mounted.unmount()
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    expect(screen.getByTestId('processing-thinking-effort')).toHaveValue('high')
    await waitFor(() => expect(screen.getByTestId('preview-profile')).toHaveValue('p2'))
    expect(screen.getByTestId('budget-max-rechecks')).toHaveValue(3)
    expect(screen.getByTestId('budget-format-retries')).toHaveValue(4)
    expect(screen.getByTestId('preview-concurrency')).toHaveValue(4)
    expect(screen.getByTestId('budget-max-input')).toHaveValue(null)
  })

  it('删除记忆中的模型后自动选择仍存在的模型', async () => {
    localStorage.setItem('ndr:processing-preferences:v1', JSON.stringify({ profileId: 'deleted', maxRecheckRounds: 5 }))
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await waitFor(() => expect(screen.getByTestId('preview-profile')).toHaveValue('p1'))
    expect(screen.getByTestId('budget-max-rechecks')).toHaveValue(5)
  })

  it('默认选中第一章并显示范围内的标注与图例', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')

    expect(await screen.findByTestId('annotation-span')).toHaveTextContent('「雨停了。」')
    expect(screen.getAllByTestId('annotation-label')[0]).toHaveTextContent('〔S1〕')
    const legend = await screen.findByTestId('speaker-legend')
    expect(within(legend).getByText('〔S1〕')).toBeInTheDocument()
    expect(screen.getByTestId('range-summary')).toHaveTextContent('0 – 20')
    expect(screen.queryByTestId('preview-reading-mode')).not.toBeInTheDocument()
    expect(screen.getByTestId('usage-total')).toHaveTextContent('0')
    expect(screen.getByTestId('usage-unknown-warning')).toHaveTextContent('没有')
    expect(screen.queryByText('金额')).not.toBeInTheDocument()
  })

  it('继承阅读页传入的章节为单章范围和批量开始章节，允许手动调整', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue([
      ...CHAPTERS, { ...CHAPTERS[0], id: 'c2', ordinal: 1, start_cp: 20, end_cp: 40 },
    ])
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview?chapterId=c2')
    await screen.findByTestId('window-picker')
    expect(screen.getByTestId('range-chapter')).toHaveValue('c2')
    expect(screen.getByTestId('range-summary')).toHaveTextContent('20 – 40')
    expect(jobsApi.estimateRange).toHaveBeenCalledWith('b1', expect.objectContaining({
      range: { chapterId: 'c2', startCp: 20, endCp: 40, dialogueStrategy: 'legacy' },
    }), expect.any(AbortSignal))
    await userEvent.selectOptions(screen.getByTestId('range-chapter'), 'c1')
    expect(screen.getByTestId('range-chapter')).toHaveValue('c1')
    // 即使临时处理其它章节，“去阅读”仍回到进入处理页之前的阅读章节。
    expect(screen.getByRole('link', { name: '去阅读' })).toHaveAttribute('href', '/books/b1/read?chapterId=c2')
    await userEvent.click(screen.getByTestId('processing-mode-batch'))
    expect(await screen.findByTestId('batch-start')).toHaveValue('c2')
    await userEvent.selectOptions(screen.getByTestId('batch-start'), 'c1')
    expect(screen.getByTestId('batch-start')).toHaveValue('c1')
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
  })

  it('链接章节不存在或不属于本书时，两种处理范围都回退到第一章', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview?chapterId=foreign-chapter')
    await screen.findByTestId('window-picker')
    expect(screen.getByTestId('range-chapter')).toHaveValue('c1')
    await userEvent.click(screen.getByTestId('processing-mode-batch'))
    expect(await screen.findByTestId('batch-start')).toHaveValue('c1')
    expect(screen.getByRole('link', { name: '去阅读' })).toHaveAttribute('href', '/books/b1/read?chapterId=c1')
  })

  it('估算只走本地接口，试运行用 preview 模式创建任务并轮询', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')
    expect(screen.getByTestId('preview-process')).toHaveClass('ndr-primary')
    expect(screen.getByTestId('preview-run')).not.toHaveClass('ndr-primary')
    expect(screen.getByTestId('preview-estimate')).not.toHaveClass('ndr-primary')

    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('estimate-summary')).toHaveTextContent('1260 token')
    expect(await screen.findByTestId('estimate-windows')).toHaveTextContent('2')
    expect(screen.getByTestId('window-picker')).toHaveTextContent('窗口 1')
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
    await userEvent.click(screen.getByTestId('window-w2'))

    await userEvent.click(screen.getByTestId('preview-run'))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(jobsApi.createJob).mock.calls[0][0]).toMatchObject({
      bookId: 'b1',
      mode: 'preview',
      readingMode: 'reread',
      selectedWindowIds: ['w1'],
    })
    expect(await screen.findByTestId('job-panel')).toBeInTheDocument()
    expect(screen.getByTestId('job-state')).toHaveTextContent('已完成')
    expect(screen.getByTestId('job-calls')).toHaveTextContent('2')
    expect(screen.getByTestId('usage-current-total')).toHaveTextContent('40')
    expect(screen.getByRole('heading', { name: '本次任务消耗' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '本书累计消耗' })).toBeInTheDocument()
    expect(screen.getByTestId('preview-notice')).toHaveTextContent('阅读标注')
  })

  it('单章正式处理按并发配置拆分所选窗口并在全覆盖后标记完成', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')
    await screen.findByTestId('window-picker')
    await userEvent.selectOptions(screen.getByTestId('dialogue-strategy'), 'complete')
    await waitFor(() => expect(screen.getByTestId('preview-process')).toBeEnabled())

    fireEvent.change(screen.getByTestId('preview-concurrency'), { target: { value: '2' } })
    await userEvent.click(screen.getByTestId('preview-process'))

    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(vi.mocked(jobsApi.createJob).mock.calls.every(([input]) => input.range.dialogueStrategy === 'complete')).toBe(true)
    expect(jobsApi.estimateRange).toHaveBeenLastCalledWith('b1', expect.objectContaining({ range: expect.objectContaining({ dialogueStrategy: 'complete' }) }), expect.any(AbortSignal))
    expect(vi.mocked(jobsApi.createJob).mock.calls.map(([input]) => input.selectedWindowIds)).toEqual([
      ['w1'],
      ['w2'],
    ])
    expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1')
  })

  it('真实异步 QUEUED 返回后立即显示进度，并发槽位等待实际完成才释放', async () => {
    const states: Record<string, JobDetailOut['state']> = {}
    vi.mocked(jobsApi.createJob).mockImplementation(async (input) => {
      const id = input.selectedWindowIds![0]
      states[id] = 'QUEUED'
      return { ...JOB, id, state: 'QUEUED' }
    })
    vi.mocked(booksApi.fetchJob).mockImplementation(async (id) => ({ ...JOB, id, state: states[id] }))
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-picker')
    fireEvent.change(screen.getByTestId('preview-concurrency'), { target: { value: '1' } })
    await userEvent.click(screen.getByTestId('preview-process'))
    await waitFor(() => expect(screen.getByTestId('single-task-w1')).toHaveTextContent('排队中'))
    expect(screen.getByTestId('single-task-w2')).toHaveTextContent('等待派发')
    expect(await screen.findByTestId('job-panel')).toBeInTheDocument()
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    expect(screen.getByTestId('processing-mode-batch')).toBeDisabled()
    expect(booksApi.completeChapterProcessing).not.toHaveBeenCalled()
    expect(jobsApi.createJob).toHaveBeenCalledTimes(1)
    const details = screen.getByTestId('single-task-details')
    const scroll = vi.fn()
    details.scrollIntoView = scroll
    const viewFirst = within(screen.getByTestId('single-task-w1')).getByRole('button', { name: '查看任务' })
    await userEvent.click(viewFirst)
    expect(details).toHaveFocus()
    expect(scroll).toHaveBeenCalledTimes(1)
    expect(details).toHaveAttribute('data-job-id', 'w1')
    expect(details).toHaveTextContent('窗口 1 任务详情')
    expect(viewFirst).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(viewFirst)
    expect(scroll).toHaveBeenCalledTimes(2)
    states.w1 = 'RUNNING'
    await waitFor(() => expect(screen.getByTestId('single-task-w1')).toHaveTextContent('处理中'), { timeout: 3000 })
    expect(jobsApi.createJob).toHaveBeenCalledTimes(1)
    states.w1 = 'COMPLETED'
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2), { timeout: 3000 })
    expect(screen.getByTestId('single-task-w2')).toHaveTextContent('排队中')
    expect(details).toHaveAttribute('data-job-id', 'w1')
    expect(booksApi.completeChapterProcessing).not.toHaveBeenCalled()
    states.w2 = 'COMPLETED'
    await waitFor(() => expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1'), { timeout: 3000 })
    expect(screen.getByTestId('single-task-w1')).toHaveTextContent('已完成')
    expect(screen.getByTestId('single-task-w2')).toHaveTextContent('已完成')
    expect(screen.queryByTestId('preview-error')).not.toBeInTheDocument()
    expect(details).toHaveAttribute('data-job-id', 'w1')
    await userEvent.click(within(screen.getByTestId('single-task-w2')).getByRole('button', { name: '查看任务' }))
    expect(details).toHaveAttribute('data-job-id', 'w2')
    expect(details).toHaveTextContent('窗口 2 任务详情')
    expect(details).toHaveFocus()
    expect(jobsApi.createJob).toHaveBeenCalledTimes(2)
  })

  it('失败任务仍保留执行面板及具体错误，不把其它后台任务遗留后立即解锁', async () => {
    let finishOther!: (job: JobDetailOut) => void
    vi.mocked(jobsApi.createJob).mockImplementation(async (input) => {
      if (input.selectedWindowIds![0] === 'w1') return { ...JOB, id: 'failed', state: 'FAILED', last_error: '提供方拒绝请求' }
      return new Promise((resolve) => { finishOther = resolve })
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-picker')
    await userEvent.click(screen.getByTestId('preview-process'))
    await waitFor(() => expect(screen.getByTestId('single-task-w1')).toHaveTextContent('提供方拒绝请求'))
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    finishOther({ ...JOB, id: 'done' })
    expect(await screen.findByTestId('preview-error')).toHaveTextContent('提供方拒绝请求')
    expect(screen.getByTestId('single-task-progress')).toBeInTheDocument()
    expect(booksApi.completeChapterProcessing).not.toHaveBeenCalled()
  })

  it('默认跳过已完成窗口，补做失败窗口后标记整章完成', async () => {
    vi.mocked(jobsApi.estimateRange).mockResolvedValue({ ...ESTIMATE, windows: ESTIMATE.windows!.map(window => ({
      ...window, processing_status: window.window_id === 'w1' ? 'completed' : 'failed',
    })) })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-picker')
    expect(screen.getByTestId('window-w1')).not.toBeChecked()
    expect(screen.getByTestId('window-w2')).toBeChecked()
    await userEvent.click(screen.getByTestId('preview-process'))
    await waitFor(() => expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1'))
    expect(jobsApi.createJob).toHaveBeenCalledTimes(1)
    expect(jobsApi.createJob).toHaveBeenCalledWith(expect.objectContaining({ selectedWindowIds: ['w2'] }))
  })

  it('刷新窗口状态后更新默认勾选，但保留用户明确选择的窗口', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-picker')
    vi.mocked(jobsApi.estimateRange).mockResolvedValue({ ...ESTIMATE, windows: ESTIMATE.windows!.map(window => ({ ...window, processing_status: 'completed' })) })
    await userEvent.click(screen.getByTestId('preview-estimate'))
    await waitFor(() => expect(screen.getByTestId('window-w1')).not.toBeChecked())
    expect(screen.getByTestId('window-w2')).not.toBeChecked()
    await userEvent.click(screen.getByTestId('windows-select-all'))
    await userEvent.click(screen.getByTestId('preview-estimate'))
    expect(screen.getByTestId('window-w1')).toBeChecked()
    expect(screen.getByTestId('window-w2')).toBeChecked()
  })

  it('旧任务标注已完整保存时可以不调用模型同步章节完成状态', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(CHAPTERS.map(chapter => ({ ...chapter, dialogue_processed: false })))
    vi.mocked(jobsApi.estimateRange).mockResolvedValue({ ...ESTIMATE, windows: ESTIMATE.windows!.map(window => ({ ...window, processing_status: 'completed' })) })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByRole('button', { name: '同步章节完成状态（不调用模型）' }))
    await waitFor(() => expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1'))
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
  })

  it('人物尚未确认时也提前显示窗口，清空选择后禁止启动', async () => {
    vi.mocked(charactersApi.fetchCharacterRoster).mockResolvedValue({
      ...ROSTER, status: 'DRAFT', confirmed_characters: [], pov_character_id: null,
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    expect(await screen.findByTestId('window-picker')).toHaveTextContent('人物识别仍会读取本章全文')
    expect(screen.getByTestId('windows-selection-summary')).toHaveTextContent('已选 2/2')
    await userEvent.click(screen.getByTestId('windows-clear'))
    expect(screen.getByTestId('windows-selection-summary')).toHaveTextContent('已选 0/2')
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    await userEvent.click(screen.getByTestId('windows-select-all'))
    expect(screen.getByTestId('window-w1')).toBeChecked()
    expect(screen.getByTestId('window-w2')).toBeChecked()
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
  })

  it('可以选择不连续的多个窗口，正式处理不扩大范围或标记整章完成', async () => {
    vi.mocked(jobsApi.estimateRange).mockResolvedValue({
      ...ESTIMATE, window_count: 3, windows: [
        ...(ESTIMATE.windows ?? []),
        { ...ESTIMATE.windows![1], window_id: 'w3', ordinal: 3 },
      ],
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-w3')
    await userEvent.click(screen.getByTestId('windows-clear'))
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    await userEvent.click(screen.getByTestId('window-w1'))
    await userEvent.click(screen.getByTestId('window-w3'))
    await userEvent.click(screen.getByTestId('preview-process'))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(vi.mocked(jobsApi.createJob).mock.calls.map(([input]) => input.selectedWindowIds)).toEqual([
      ['w1'], ['w3'],
    ])
    expect(booksApi.completeChapterProcessing).not.toHaveBeenCalled()
  })

  it('重新预览同一窗口计划或修改预算后保留窗口勾选', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('window-picker')
    await userEvent.click(screen.getByTestId('window-w2'))
    fireEvent.change(screen.getByTestId('range-chapter'), { target: { value: 'c1' } })
    expect(screen.getByTestId('window-w1')).toBeChecked()
    expect(screen.getByTestId('window-w2')).not.toBeChecked()
    await userEvent.click(screen.getByTestId('preview-estimate'))
    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(2))
    expect(screen.getByTestId('window-w2')).not.toBeChecked()
    fireEvent.change(screen.getByTestId('budget-max-input'), { target: { value: '50000' } })
    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(3))
    await screen.findByTestId('window-picker')
    expect(screen.getByTestId('window-w1')).toBeChecked()
    expect(screen.getByTestId('window-w2')).not.toBeChecked()
  })

  it('窗口预览失败时不允许处理，重试成功后恢复多选', async () => {
    vi.mocked(jobsApi.estimateRange).mockRejectedValueOnce(new Error('窗口规划不可用'))
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    expect(await screen.findByRole('alert')).toHaveTextContent('窗口预览失败：窗口规划不可用')
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await screen.findByTestId('window-picker')
    await waitFor(() => expect(screen.getByTestId('preview-process')).toBeEnabled())
  })

  it('切换章节后迟到的预览结果不会覆盖当前章窗口', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue([
      ...CHAPTERS,
      { ...CHAPTERS[0], id: 'c2', ordinal: 1, start_cp: 20, end_cp: 40 },
    ])
    let resolveOld!: (estimate: EstimateOut) => void
    vi.mocked(jobsApi.estimateRange).mockImplementation((_bookId, input) =>
      input.range.chapterId === 'c1'
        ? new Promise((resolve) => { resolveOld = resolve })
        : Promise.resolve({ ...ESTIMATE, windows: [{ ...ESTIMATE.windows![0], window_id: 'chapter2' }] }),
    )
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(1))
    expect(screen.getByTestId('preview-process')).toBeDisabled()
    await userEvent.selectOptions(screen.getByTestId('range-chapter'), 'c2')
    await screen.findByTestId('window-chapter2')
    resolveOld(ESTIMATE)
    await waitFor(() => expect(screen.getByTestId('window-chapter2')).toBeChecked())
    expect(screen.queryByTestId('window-w1')).not.toBeInTheDocument()
  })

  it('原文/标注切换只改显示，不触发任何模型调用', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')

    const estimateCalls = vi.mocked(jobsApi.estimateRange).mock.calls.length
    const jobCalls = vi.mocked(jobsApi.createJob).mock.calls.length
    const annotationCalls = vi.mocked(annotationsApi.fetchAnnotations).mock.calls.length

    await userEvent.click(screen.getByTestId('view-original'))
    await waitFor(() => expect(screen.queryByTestId('annotation-span')).toBeNull())
    // 原文仍然完整
    expect(screen.getByTestId('document-renderer')).toHaveTextContent('「雨停了。」少女说。')

    await userEvent.click(screen.getByTestId('view-annotated'))
    await waitFor(() => expect(screen.getByTestId('annotation-span')).toBeInTheDocument())

    expect(vi.mocked(jobsApi.estimateRange).mock.calls.length).toBe(estimateCalls)
    expect(vi.mocked(jobsApi.createJob).mock.calls.length).toBe(jobCalls)
    expect(vi.mocked(annotationsApi.fetchAnnotations).mock.calls.length).toBe(annotationCalls)
  })

  it('没有模型配置时不显示可运行入口的假成功', async () => {
    vi.mocked(profilesApi.fetchProfiles).mockResolvedValue([])
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')

    expect(await screen.findByTestId('preview-no-profile')).toBeInTheDocument()
    expect(screen.getByTestId('preview-run')).toBeDisabled()
    expect(screen.getByTestId('preview-process')).toBeDisabled()
  })

  it.each([false, true])('批量先确认人物，并传入后台覆盖人工资料设置 %s', async (allow) => {
    updateGeneralSettings({ allowOverwriteManualCharacters: allow })
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 30, output_tokens: 10, total_tokens: 40, unknown_runs: 0 },
    } as JobDetailOut
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockResolvedValue(meteredJob)
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    await userEvent.selectOptions(screen.getByTestId('dialogue-strategy'), 'complete-review')
    fireEvent.change(screen.getByTestId('processing-thinking-mode'), { target: { value: 'enabled' } })
    fireEvent.change(screen.getByTestId('processing-thinking-effort'), { target: { value: 'low' } })
    expect(screen.queryByTestId('preview-profile')).not.toBeInTheDocument()
    expect(screen.getByTestId('batch-profile')).toHaveValue('p1')

    await userEvent.clear(screen.getByTestId('batch-max-rechecks'))
    await userEvent.type(screen.getByTestId('batch-max-rechecks'), '2')
    fireEvent.change(screen.getByTestId('batch-concurrency'), { target: { value: '1' } })

    await userEvent.type(screen.getByTestId('batch-token-limit'), '50000')
    await userEvent.click(screen.getByTestId('batch-run'))
    expect(await screen.findByTestId('batch-estimate')).toHaveTextContent('3,280')
    expect(jobsApi.estimateRange).toHaveBeenCalledWith(
      'b1',
      expect.objectContaining({ budget: expect.objectContaining({ maxRecheckRounds: 2 }) }),
    )
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(1))
    expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledWith('b1', 'c1',
      expect.objectContaining({ confirmationMode: 'automatic' }))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledWith(
      'b1',
      'c1',
      expect.objectContaining({ profileId: 'p1', maxInputTokens: 2020, allowOverwriteManual: allow, inferenceOptions: { thinking_mode: 'enabled', reasoning_effort: 'low' } }),
    )
    expect(jobsApi.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'process',
        range: { chapterId: 'c1', startCp: 0, endCp: 20, dialogueStrategy: 'complete-review' },
        selectedWindowIds: ['w1'],
        inferenceOptions: { thinking_mode: 'enabled', reasoning_effort: 'low' },
        budget: expect.objectContaining({ maxRecheckRounds: 2 }),
      }),
    )
    expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1')
    await waitFor(() =>
      expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'),
    )
  })

  it('批量流水线会让下一章人物先与当前章窗口进入共享队列', async () => {
    const twoChapters = [
      { ...CHAPTERS[0], dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c2', ordinal: 1, title: '第二章', start_cp: 20, end_cp: 40, dialogue_processed: false },
    ] as ChapterOut[]
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 30, output_tokens: 10, total_tokens: 40, unknown_runs: 0 },
    } as JobDetailOut
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(twoChapters)
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockResolvedValue(meteredJob)
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')

    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(4))
    const secondRosterOrder = vi.mocked(charactersApi.analyzeCharacterRoster).mock.invocationCallOrder[1]
    const firstWindowOrder = vi.mocked(jobsApi.createJob).mock.invocationCallOrder[0]
    expect(secondRosterOrder).toBeLessThan(firstWindowOrder)
    await waitFor(() => expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'))
  })

  it('共享池有空闲时也必须等待上一章人物确认，人物与对白仍可并发', async () => {
    const threeChapters = [
      { ...CHAPTERS[0], dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c2', ordinal: 1, title: '第二章', start_cp: 20, end_cp: 40, dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c3', ordinal: 2, title: '第三章', start_cp: 40, end_cp: 60, dialogue_processed: false },
    ] as ChapterOut[]
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 30, output_tokens: 10, total_tokens: 40, unknown_runs: 0 },
    } as JobDetailOut
    const rosterResolvers = new Map<string, (job: JobDetailOut) => void>()
    const dialogueResolvers: Array<(job: JobDetailOut) => void> = []
    let confirmFirst!: () => void
    vi.mocked(charactersApi.confirmCharacterRoster).mockImplementationOnce(() =>
      new Promise((resolve) => { confirmFirst = () => resolve(ROSTER) }),
    )
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(threeChapters)
    vi.mocked(charactersApi.analyzeCharacterRoster).mockImplementation((_bookId, chapterId) => {
      return new Promise<JobDetailOut>((resolve) => {
        rosterResolvers.set(chapterId, resolve)
      })
    })
    vi.mocked(jobsApi.createJob).mockImplementation(() => {
      return new Promise<JobDetailOut>((resolve) => dialogueResolvers.push(resolve))
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    fireEvent.change(screen.getByTestId('batch-concurrency'), { target: { value: '3' } })

    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(rosterResolvers.has('c1')).toBe(true))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    rosterResolvers.get('c1')!(meteredJob)
    await waitFor(() => expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(1))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(1)
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    confirmFirst()
    await waitFor(() => expect(rosterResolvers.has('c2')).toBe(true))
    await waitFor(() => expect(dialogueResolvers).toHaveLength(2))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(2)
    rosterResolvers.get('c2')!(meteredJob)
    // 释放前章窗口后，第三章人物才有共享并发额度，但第二章确认已先完成。
    dialogueResolvers.splice(0).forEach((resolve) => resolve(meteredJob))
    await waitFor(() => expect(rosterResolvers.has('c3')).toBe(true))
    expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(2)
    rosterResolvers.get('c3')!(meteredJob)
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(4))
    dialogueResolvers.splice(0).forEach((resolve) => resolve(meteredJob))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(6))
    dialogueResolvers.splice(0).forEach((resolve) => resolve(meteredJob))
    await waitFor(() => expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'))
  })

  it('单个章节失败不会把后续章节统一标为失败', async () => {
    const threeChapters = [
      { ...CHAPTERS[0], dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c2', ordinal: 1, title: '第二章', start_cp: 20, end_cp: 40, dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c3', ordinal: 2, title: '第三章', start_cp: 40, end_cp: 60, dialogue_processed: false },
    ] as ChapterOut[]
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 30, output_tokens: 10, total_tokens: 40, unknown_runs: 0 },
    } as JobDetailOut
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(threeChapters)
    vi.mocked(charactersApi.analyzeCharacterRoster)
      .mockRejectedValueOnce(new Error('第一章人物识别失败'))
      .mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockResolvedValue(meteredJob)
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    fireEvent.change(screen.getByTestId('batch-concurrency'), { target: { value: '2' } })

    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(3))
    await waitFor(() =>
      expect(screen.getByTestId('batch-progress')).toHaveTextContent('成功 2 章，失败 1 章'),
    )
    expect(screen.getByTestId('batch-error')).toHaveTextContent('2 章成功，1 章失败')
    const resultPanel = screen.getByTestId('batch-result-panel')
    expect(within(resultPanel).getByText('第一章人物识别失败')).toBeInTheDocument()
    expect(within(resultPanel).getByRole('columnheader', { name: '原因 / 错误详情' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看任务列表' })).toHaveAttribute('href', '#batch-task-list')
    await userEvent.click(within(resultPanel).getByRole('button', { name: '收起批量任务明细' }))
    expect(within(resultPanel).getByText('第一章人物识别失败')).not.toBeVisible()
    expect(within(resultPanel).getByTestId('batch-task-summary')).toBeVisible()
    expect(within(resultPanel).getByText(/共.*项；失败 1 项/)).toBeVisible()
    await userEvent.click(screen.getByRole('link', { name: '查看任务列表' }))
    expect(within(resultPanel).getByText('第一章人物识别失败')).toBeVisible()
    expect(screen.getByTestId('batch-run')).toBeEnabled()
    expect(jobsApi.createJob).toHaveBeenCalledTimes(4)
    expect(booksApi.completeChapterProcessing).toHaveBeenCalledTimes(2)
    await userEvent.click(screen.getByTestId('processing-mode-single'))
    expect(screen.getByTestId('batch-result-panel')).toHaveTextContent('第一章人物识别失败')
    expect(screen.queryByTestId('batch-processor')).not.toBeInTheDocument()
  })

  it('已完成的无文字章节显示说明并禁止重复人物分析', async () => {
    vi.mocked(charactersApi.fetchCharacterRoster).mockResolvedValue({
      ...ROSTER, status: 'CONFIRMED', candidates: [], confirmed_characters: [], pov_character_id: null,
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    expect(await screen.findByText(/本章没有正文文字，已完成/)).toBeInTheDocument()
    expect(screen.getByTestId('roster-analyze')).toBeDisabled()
    expect(screen.queryByText('已确认本章主人公；如需修改，请重新确认。')).not.toBeInTheDocument()
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
  })

  it('无人物的插图章跳过对白，后续章节仍按顺序处理', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue([
      { ...CHAPTERS[0], title: '插图', dialogue_processed: false },
      { ...CHAPTERS[0], id: 'c2', ordinal: 1, title: '第二章', dialogue_processed: false },
    ] as ChapterOut[])
    vi.mocked(charactersApi.fetchCharacterRoster).mockImplementation(async (_bookId, chapterId) =>
      chapterId === 'c1' ? { ...ROSTER, candidates: [] } : ROSTER,
    )
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(screen.getByTestId('batch-progress')).toHaveTextContent('跳过无人物章节 1 章'))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(2)
    expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(1)
    expect(jobsApi.createJob).toHaveBeenCalledTimes(2)
    expect(vi.mocked(jobsApi.createJob).mock.calls.every(([input]) => input.range.chapterId === 'c2')).toBe(true)
    expect(screen.queryByTestId('batch-error')).not.toBeInTheDocument()
  })

  it('批量运行时切换为逐任务进度面板，停止后不再派发排队窗口', async () => {
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 30, output_tokens: 10, total_tokens: 40, unknown_runs: 0 },
    } as JobDetailOut
    let resolveWindow!: (job: JobDetailOut) => void
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockImplementation(
      () => new Promise<JobDetailOut>((resolve) => { resolveWindow = resolve }),
    )
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    fireEvent.change(screen.getByTestId('batch-concurrency'), { target: { value: '1' } })

    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    const panel = await screen.findByTestId('batch-progress-panel')
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(1))
    expect(within(panel).getByText('人物识别')).toBeInTheDocument()
    expect(within(panel).getAllByText('对白归属')).toHaveLength(2)
    expect(within(panel).getByText('窗口 1 · 1 句对白')).toBeInTheDocument()
    expect(within(panel).getByText('窗口 2 · 2 句对白')).toBeInTheDocument()
    expect(screen.getByTestId('processing-mode-single')).toBeDisabled()
    expect(screen.queryByTestId('batch-run')).not.toBeInTheDocument()

    await userEvent.click(within(panel).getByTestId('batch-stop'))
    expect(within(panel).getByTestId('batch-progress-message')).toHaveTextContent('正在安全停止')
    expect(within(panel).getByTestId('batch-stop')).toBeDisabled()
    expect(within(panel).getByText('已停止')).toBeInTheDocument()

    resolveWindow(meteredJob)
    await waitFor(() => expect(screen.queryByTestId('batch-progress-panel')).not.toBeInTheDocument())
    expect(screen.getByTestId('batch-result-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('batch-stop')).not.toBeInTheDocument()
    expect(jobsApi.createJob).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('batch-error')).toHaveTextContent('批量处理已停止')
  })

  it('批量处理会跳过回导后标记为已处理的章节', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(
      CHAPTERS.map((chapter) => ({ ...chapter, dialogue_processed: true })),
    )
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')

    await userEvent.click(screen.getByTestId('batch-run'))
    expect(await screen.findByTestId('batch-estimate')).toHaveTextContent('0 tokens')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() =>
      expect(screen.getByTestId('batch-progress')).toHaveTextContent('均已处理'),
    )
    expect(charactersApi.analyzeCharacterRoster).not.toHaveBeenCalled()
    expect(jobsApi.createJob).not.toHaveBeenCalled()
    // 默认单章页可自动做本地窗口预览，但批量跳过的章节不得重新估算。
    expect(vi.mocked(jobsApi.estimateRange).mock.calls.every((call) => call.length === 3)).toBe(true)
  })

  it('开启强制重做会重新估算并处理已经完成的章节', async () => {
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(
      CHAPTERS.map((chapter) => ({ ...chapter, dialogue_processed: true })),
    )
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    await userEvent.click(screen.getByTestId('batch-run'))
    expect(await screen.findByTestId('batch-estimate')).toHaveTextContent('0 tokens')

    await userEvent.click(screen.getByTestId('batch-force-reprocess'))
    expect(screen.queryByTestId('batch-estimate')).not.toBeInTheDocument()
    await userEvent.click(screen.getByTestId('batch-run'))
    expect(await screen.findByTestId('batch-estimate')).toHaveTextContent('已包含已处理章节')
    expect(jobsApi.estimateRange).toHaveBeenCalled()
    await userEvent.click(screen.getByTestId('batch-run'))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(jobsApi.createJob).toHaveBeenCalledWith(expect.objectContaining({ forceReprocess: true }))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalled()
    await waitFor(() => expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'))
  })

  it('达到 Token 上限的 80% 时可原地修改额度并继续', async () => {
    const meteredJob = {
      ...JOB,
      unknown_usage_runs: 0,
      usage: { input_tokens: 1600, output_tokens: 400, total_tokens: 2000, unknown_runs: 0 },
    } as JobDetailOut
    const dialogueJob = {
      ...meteredJob,
      usage: { input_tokens: 80, output_tokens: 20, total_tokens: 100, unknown_runs: 0 },
    } as JobDetailOut
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockResolvedValue(dialogueJob)
    const prompt = vi.spyOn(window, 'prompt').mockReturnValue('5000')
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')

    await userEvent.type(screen.getByTestId('batch-token-limit'), '2500')
    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(prompt).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    await waitFor(() =>
      expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'),
    )
    expect(screen.queryByTestId('batch-error')).not.toBeInTheDocument()
    prompt.mockRestore()
  })
  it('展示可编辑的引号修复建议并支持重扫和重新检测', async () => {
    const normalization = {
      id: 'n1',
      book_version_id: 'v1',
      opening_cp: 3,
      close_cp: 10,
      replacement: '”',
      source: 'AUTO',
      status: 'ACTIVE',
      original_text: '“他说雨停了。',
      normalized_text: '“他说雨停了。”',
      reason: '开引号未在本段或后续段落闭合，且下一处引号是开引号。',
      version: 1,
    } as QuoteNormalizationOut
    const refreshed = {
      book_id: 'b1',
      book_version_id: 'v1',
      created: 0,
      active: 1,
      scan: {
        book_id: 'b1',
        book_version_id: 'v1',
        job_id: 'scan1',
        scanner_version: 'quote-scan-1',
        quote_count: 1,
        top_level_quote_count: 1,
        gap_count: 0,
        warnings: [],
        stats: {},
      },
    } as QuoteNormalizationRefreshOut
    vi.mocked(booksApi.fetchQuoteNormalizations).mockResolvedValue([normalization])
    vi.mocked(booksApi.refreshQuoteNormalizations).mockResolvedValue(refreshed)
    vi.mocked(booksApi.updateQuoteNormalization).mockResolvedValue(refreshed)
    renderRoute('/books/:bookId/preprocessing', <PreprocessingPage />, '/books/b1/preprocessing')

    expect(await screen.findByTestId('quote-normalization-warning'))
      .toHaveTextContent('已启用 1 条虚拟闭合')
    await userEvent.click(document.querySelector('.ndr-normalization-card summary')!)
    expect(screen.getByText('“他说雨停了。”')).toBeInTheDocument()

    fireEvent.change(screen.getByTestId('quote-normalization-close-n1'), {
      target: { value: '9' },
    })
    await userEvent.click(screen.getByText('保存并重扫'))

    await waitFor(() => expect(booksApi.updateQuoteNormalization).toHaveBeenCalledWith(
      'b1',
      'n1',
      {
        close_cp: 9,
        replacement: '”',
        status: 'ACTIVE',
        expected_version: 1,
      },
    ))

    await userEvent.click(screen.getByTestId('quote-normalization-refresh'))
    await waitFor(() => expect(booksApi.refreshQuoteNormalizations).toHaveBeenCalledWith('b1'))
  })

})
