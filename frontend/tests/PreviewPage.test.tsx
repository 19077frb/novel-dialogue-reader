import { screen, waitFor, within } from '@testing-library/react'
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
  usage: {},
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
  currency: null,
  cost: null,
} as UsageOut

describe('PreviewPage', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchBook).mockReset()
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(booksApi.fetchContent).mockReset()
    vi.mocked(booksApi.fetchQuotes).mockReset()
    vi.mocked(booksApi.fetchJob).mockReset()
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
    vi.mocked(annotationsApi.fetchAnnotations).mockResolvedValue(ANNOTATIONS)
    vi.mocked(charactersApi.fetchBookCharacters).mockResolvedValue(ROSTER.confirmed_characters ?? [])
    vi.mocked(charactersApi.fetchCharacterRoster).mockResolvedValue(ROSTER)
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
    expect(screen.getByTestId('usage-total')).toHaveTextContent('0')
    expect(screen.getByTestId('usage-unknown-warning')).toHaveTextContent('没有')
  })

  it('估算只走本地接口，试运行用 preview 模式创建任务并轮询', async () => {
    renderRoute('/books/:bookId/preview', <PreviewPage />, '/books/b1/preview')
    await screen.findByTestId('annotation-span')

    await userEvent.click(screen.getByTestId('preview-estimate'))
    await waitFor(() => expect(jobsApi.estimateRange).toHaveBeenCalledTimes(1))
    expect(await screen.findByTestId('estimate-summary')).toHaveTextContent('1260 token')
    expect(await screen.findByTestId('estimate-windows')).toHaveTextContent('2')

    await userEvent.click(screen.getByTestId('preview-run'))
    await waitFor(() => expect(jobsApi.createJob).toHaveBeenCalledTimes(1))
    expect(vi.mocked(jobsApi.createJob).mock.calls[0][0]).toMatchObject({
      bookId: 'b1',
      mode: 'preview',
      readingMode: 'initial',
    })
    expect(await screen.findByTestId('job-panel')).toBeInTheDocument()
    expect(screen.getByTestId('job-state')).toHaveTextContent('COMPLETED')
    expect(screen.getByTestId('job-calls')).toHaveTextContent('2')
    expect(screen.getByTestId('preview-notice')).toHaveTextContent('标注投影')
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
})
