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
} from '../src/api/types'
import PreviewPage from '../src/pages/PreviewPage'
import { renderRoute } from './helpers'

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
    job: (id: string) => ['job', id],
  },
  fetchBook: vi.fn(),
  fetchChapters: vi.fn(),
  fetchContent: vi.fn(),
  fetchQuotes: vi.fn(),
  fetchJob: vi.fn(),
  completeChapterProcessing: vi.fn(),
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
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchContent).mockReset()
    vi.mocked(booksApi.fetchQuotes).mockReset()
    vi.mocked(booksApi.fetchJob).mockReset()
    vi.mocked(booksApi.completeChapterProcessing).mockReset()
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

  it('估算只走本地接口，试运行用 preview 模式创建任务并轮询', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')

    await userEvent.click(screen.getByTestId('preview-estimate'))
    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('estimate-summary')).toHaveTextContent('1260 token')
    expect(await screen.findByTestId('estimate-windows')).toHaveTextContent('2')
    expect(screen.getByTestId('window-picker')).toHaveTextContent('窗口 1')
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
    expect(screen.getByTestId('job-state')).toHaveTextContent('COMPLETED')
    expect(screen.getByTestId('job-calls')).toHaveTextContent('2')
    expect(screen.getByTestId('usage-current-total')).toHaveTextContent('40')
    expect(screen.getByRole('heading', { name: '本次任务消耗' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '本书累计消耗' })).toBeInTheDocument()
    expect(screen.getByTestId('preview-notice')).toHaveTextContent('标注投影')
  })

  it('单章正式处理按并发配置拆分所选窗口并在全覆盖后标记完成', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')
    await userEvent.click(screen.getByTestId('preview-estimate'))
    await screen.findByTestId('window-picker')

    fireEvent.change(screen.getByTestId('preview-concurrency'), { target: { value: '2' } })
    await userEvent.click(screen.getByTestId('preview-process'))

    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(vi.mocked(jobsApi.createJob).mock.calls.map(([input]) => input.selectedWindowIds)).toEqual([
      ['w1'],
      ['w2'],
    ])
    expect(booksApi.completeChapterProcessing).toHaveBeenCalledWith('b1', 'c1', 'v1')
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

  it('批量处理会先识别并确认人物，再处理所选章节对白', async () => {
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
      expect.objectContaining({ budget: expect.objectContaining({ maxRechecks: 2 }) }),
    )
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(charactersApi.confirmCharacterRoster).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(2))
    expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledWith(
      'b1',
      'c1',
      expect.objectContaining({ profileId: 'p1', maxInputTokens: 2020 }),
    )
    expect(jobsApi.createJob).toHaveBeenCalledWith(
      expect.objectContaining({
        mode: 'process',
        range: { chapterId: 'c1', startCp: 0, endCp: 20 },
        selectedWindowIds: ['w1'],
        budget: expect.objectContaining({ maxRechecks: 2 }),
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

  it('当前章对白完成前不会提前识别下下章人物', async () => {
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
    const firstChapterResolvers: Array<(job: JobDetailOut) => void> = []
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(threeChapters)
    vi.mocked(charactersApi.analyzeCharacterRoster).mockResolvedValue(meteredJob)
    vi.mocked(jobsApi.createJob).mockImplementation((input) => {
      if (input.range.chapterId !== 'c1') return Promise.resolve(meteredJob)
      return new Promise<JobDetailOut>((resolve) => firstChapterResolvers.push(resolve))
    })
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await userEvent.click(await screen.findByTestId('processing-mode-batch'))
    await screen.findByTestId('batch-processor')
    fireEvent.change(screen.getByTestId('batch-concurrency'), { target: { value: '3' } })

    await userEvent.click(screen.getByTestId('batch-run'))
    await screen.findByTestId('batch-estimate')
    await userEvent.click(screen.getByTestId('batch-run'))

    await waitFor(() => expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(2))
    expect(vi.mocked(charactersApi.analyzeCharacterRoster).mock.calls.map((call) => call[1])).toEqual(['c1', 'c2'])
    await waitFor(() => expect(firstChapterResolvers).toHaveLength(2))
    firstChapterResolvers.forEach((resolve) => resolve(meteredJob))

    await waitFor(() => expect(charactersApi.analyzeCharacterRoster).toHaveBeenCalledTimes(3))
    expect(vi.mocked(charactersApi.analyzeCharacterRoster).mock.calls[2]?.[1]).toBe('c3')
    await waitFor(() => expect(screen.getByTestId('batch-progress')).toHaveTextContent('批量处理完成'))
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
    expect(jobsApi.estimateRange).not.toHaveBeenCalled()
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
})
