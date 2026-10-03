/**
 * 待确认队列与人工更正。
 *
 * 除 `recheckQuote` 外，这些接口**都不调用模型**（不产生费用）：
 * 局部更正只影响选中对白；结构调整只标记实际受影响范围。
 */
import { apiData } from './client'
import type {
  CorrectionOut,
  GapCorrectionOut,
  GapDecision,
  JobDetailOut,
  InferenceOptions,
  QuoteKind,
  ReadingMode,
  ReviewItemDetailOut,
  ReviewItemOut,
  ReviewQueueResponse,
  ReviewQueueStatus,
  ReviewReason,
  SpeakerRevisionOut,
  StaleReviewCleanupOut,
  UndoOut,
} from './types'

export interface ReviewFilters {
  chapterId?: string | null
  sceneId?: string | null
  reason?: ReviewReason | ''
  queueStatus?: ReviewQueueStatus | ''
  limit?: number
  cursor?: string | null
}

export const reviewKeys = {
  queue: (bookId: string, filters: ReviewFilters) => ['review-items', bookId, filters] as const,
  detail: (itemId: string) => ['review-item', itemId] as const,
  quoteDetail: (quoteId: string, contextWindowCp: number) =>
    ['quote-detail', quoteId, contextWindowCp] as const,
}

export function cleanupDependencyReviews(bookId: string): Promise<StaleReviewCleanupOut> {
  return apiData<StaleReviewCleanupOut>(`/api/books/${bookId}/review-items/cleanup-dependencies`, {
    method: 'POST',
  })
}

function filterParams(filters: ReviewFilters): URLSearchParams {
  const params = new URLSearchParams()
  if (filters.chapterId) params.set('chapter_id', filters.chapterId)
  if (filters.sceneId) params.set('scene_id', filters.sceneId)
  if (filters.reason) params.set('reason', filters.reason)
  if (filters.queueStatus) params.set('queue_status', filters.queueStatus)
  params.set('limit', String(filters.limit ?? 50))
  if (filters.cursor) params.set('cursor', filters.cursor)
  return params
}

export function fetchReviewQueue(
  bookId: string,
  filters: ReviewFilters = {},
  signal?: AbortSignal,
): Promise<ReviewQueueResponse> {
  return apiData<ReviewQueueResponse>(
    `/api/books/${bookId}/review-items?${filterParams(filters).toString()}`,
    { signal },
  )
}

export function fetchReviewItemDetail(
  itemId: string,
  signal?: AbortSignal,
): Promise<ReviewItemDetailOut> {
  return apiData<ReviewItemDetailOut>(`/api/review-items/${itemId}`, { signal })
}

export interface FlagInput {
  reason?: ReviewReason
  note?: string
}

export function flagReviewItem(
  quoteId: string,
  input: FlagInput = {},
  signal?: AbortSignal,
): Promise<ReviewItemOut> {
  return apiData<ReviewItemOut>(`/api/quotes/${quoteId}/review-items`, {
    method: 'POST',
    body: { reason: input.reason ?? 'USER_FLAGGED', note: input.note ?? '' },
    signal,
  })
}

export function deferReviewItem(
  itemId: string,
  note = '',
  signal?: AbortSignal,
): Promise<ReviewItemOut> {
  return apiData<ReviewItemOut>(`/api/review-items/${itemId}/defer`, {
    method: 'POST',
    body: { note },
    signal,
  })
}

export interface QuoteCorrectionInput {
  action: 'assign_existing' | 'create_speaker' | 'set_kind' | 'mark_unknown'
  speakerRef?: string | null
  kind?: QuoteKind | null
  description?: string
  quoteIds?: string[] | null
  expectedVersion?: number | null
  expectedSceneVersion?: number | null
  note?: string
}

export function submitQuoteCorrection(
  quoteId: string,
  input: QuoteCorrectionInput,
  signal?: AbortSignal,
): Promise<CorrectionOut> {
  return apiData<CorrectionOut>(`/api/quotes/${quoteId}/corrections`, {
    method: 'POST',
    signal,
    body: {
      action: input.action,
      speaker_ref: input.speakerRef ?? null,
      kind: input.kind ?? null,
      description: input.description ?? '',
      quote_ids: input.quoteIds ?? null,
      expected_version: input.expectedVersion ?? null,
      expected_scene_version: input.expectedSceneVersion ?? null,
      note: input.note ?? '',
    },
  })
}

export interface GapCorrectionInput {
  decision: GapDecision
  expectedSceneVersion?: number | null
  note?: string
}

export function submitGapCorrection(
  gapId: string,
  input: GapCorrectionInput,
  signal?: AbortSignal,
): Promise<GapCorrectionOut> {
  return apiData<GapCorrectionOut>(`/api/gaps/${gapId}/corrections`, {
    method: 'POST',
    signal,
    body: {
      decision: input.decision,
      expected_scene_version: input.expectedSceneVersion ?? null,
      note: input.note ?? '',
    },
  })
}

export interface SpeakerRevisionInput {
  visibleFromCp?: number | null
  operation: 'MERGE' | 'SPLIT'
  sourceGroupIds?: string[]
  buckets?: string[][]
  expectedSceneVersion?: number | null
  note?: string
}

export function submitSpeakerRevision(
  sceneId: string,
  input: SpeakerRevisionInput,
  signal?: AbortSignal,
): Promise<SpeakerRevisionOut> {
  return apiData<SpeakerRevisionOut>(`/api/scenes/${sceneId}/speaker-revisions`, {
    method: 'POST',
    signal,
    body: {
      operation: input.operation,
      visible_from_cp: input.visibleFromCp ?? null,
      source_group_ids: input.sourceGroupIds ?? [],
      buckets: input.buckets ?? [],
      expected_scene_version: input.expectedSceneVersion ?? null,
      note: input.note ?? '',
    },
  })
}

export function undoCorrection(correctionId: string, signal?: AbortSignal): Promise<UndoOut> {
  return apiData<UndoOut>(`/api/corrections/${correctionId}/undo`, { method: 'POST', signal })
}

export interface RecheckInput {
  inferenceOptions?: InferenceOptions
  profileId: string
  maxInputTokens?: number | null
  maxRecheckRounds?: number
  maxFormatRetries?: number
  readingMode?: ReadingMode
  idempotencyKey: string
  runNow?: boolean
}

/** 局部复核：**会创建真实任务并可能产生费用**，与「展开原文」完全不同。 */
export function recheckQuote(
  quoteId: string,
  input: RecheckInput,
  signal?: AbortSignal,
): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/quotes/${quoteId}/recheck`, {
    method: 'POST',
    signal,
    body: {
      profile_id: input.profileId,
      ...(input.inferenceOptions ? { inference_options: input.inferenceOptions } : {}),
      budget: {
        max_input_tokens: input.maxInputTokens ?? null,
        max_output_tokens: null,
        max_recheck_rounds: input.maxRecheckRounds ?? 0,
        max_format_retries: input.maxFormatRetries ?? 1,
      },
      reading_mode: input.readingMode ?? 'initial',
      visible_horizon_cp: null,
      idempotency_key: input.idempotencyKey,
      run_now: input.runNow ?? true,
      note: '',
    },
  })
}
