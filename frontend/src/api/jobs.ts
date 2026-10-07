/**
 * 任务、估算与用量。
 *
 * 预览与正式处理共用同一套 `POST /api/jobs`（`mode: preview | process`），
 * 结果直接落进同一份标注投影，不创建第二套临时识别存储。
 */
import { apiData } from './client'
import type {
  EstimateOut,
  InferenceOptions,
  JobDetailOut,
  JobRecoveryOut,
  ReadingMode,
  UsageOut,
  TaskQueuePage,
} from './types'

export const jobKeys = {
  usage: (bookId: string) => ['usage', bookId] as const,
}

export function fetchTaskQueue(activeOnly = true, cursor?: string | null, signal?: AbortSignal, bookId?: string): Promise<TaskQueuePage> {
  const params = new URLSearchParams({ active_only: String(activeOnly), limit: '50' })
  if (cursor) params.set('cursor', cursor)
  if (bookId) params.set('book_id', bookId)
  return apiData<TaskQueuePage>(`/api/jobs/queue?${params}`, { signal })
}

export function fetchRecentJobs(input: { bookId?: string; versionId?: string; chapterId?: string; quoteId?: string; kind?: JobDetailOut['kind']; idempotencyKey?: string; limit?: number }, signal?: AbortSignal): Promise<JobDetailOut[]> {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries({ book_id: input.bookId, book_version_id: input.versionId,
    chapter_id: input.chapterId, quote_id: input.quoteId, kind: input.kind, idempotency_key: input.idempotencyKey, limit: input.limit })) {
    if (value !== undefined) params.set(key, String(value))
  }
  return apiData<JobDetailOut[]>(`/api/jobs/recent?${params}`, { signal })
}

export type DialogueStrategy = 'legacy' | 'complete' | 'complete-review' | 'complete-blocks' | 'complete-blocks-review' | 'complete-blocks-isolated' | 'complete-blocks-isolated-review' | 'complete-blocks-isolated-feedback-review'

export function dialogueStrategyDisabledReason(strategy: DialogueStrategy | undefined, rounds: number): string | null {
  return strategy === 'complete-blocks-isolated-feedback-review' && rounds < 1
    ? '人物名单反馈需要独立复核，请将每个窗口最多复核次数设为至少 1。' : null
}

export function dialogueStrategyPayload(strategy?: DialogueStrategy) {
  return strategy && ['complete', 'complete-review', 'complete-blocks', 'complete-blocks-review', 'complete-blocks-isolated', 'complete-blocks-isolated-review', 'complete-blocks-isolated-feedback-review'].includes(strategy) ? {
    context_policy: strategy.startsWith('complete-blocks') ? 'context-chapter-2' : 'context-chapter-1',
    output_protocol: 'expression-production-1',
    ...(strategy.includes('-isolated') ? { auxiliary_protocol: 'expression-auxiliary-isolation-1' } : {}),
    ...(strategy.endsWith('-review') ? { review_protocol: 'expression-evidence-review-1' } : {}),
    ...(strategy === 'complete-blocks-isolated-feedback-review' ? { identity_feedback_protocol: 'identity-feedback-1' } : {}),
  } : {}
}

export interface RangeInput {
  dialogueStrategy?: DialogueStrategy
  chapterId: string | null
  startCp: number
  endCp: number | null
}

export interface BudgetInput {
  maxInputTokens: number | null
  maxOutputTokens: number | null
  maxRecheckRounds: number
  maxRechecks?: number // Restored batches created before full-window review rounds.
  maxFormatRetries?: number
}

export function budgetPayload(budget: BudgetInput) {
  return {
    max_input_tokens: budget.maxInputTokens,
    max_output_tokens: budget.maxOutputTokens,
    ...(typeof budget.maxRecheckRounds === 'number'
      ? { max_recheck_rounds: budget.maxRecheckRounds }
      : { max_rechecks: (budget as BudgetInput & { maxRechecks?: number }).maxRechecks ?? 0 }),
    max_format_retries: budget.maxFormatRetries ?? 1,
  }
}

export interface EstimateInput {
  bookVersionId?: string | null
  range: RangeInput
  readingMode: ReadingMode
  visibleHorizonCp?: number | null
  budget: BudgetInput
}

/** 纯本地估算：不调用模型、不写数据库。 */
/**
 * 稳定的短摘要（FNV-1a 32 位 → base36）。
 *
 * 幂等键有 128 字符上限：直接把范围/预算/配置 JSON 拼进键会超限（422），
 * 这里用短摘要保证同输入同键、不同输入不同键（碰撞概率足够低，且摘要不同只会多跑一次）。
 */
export function shortHash(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash.toString(36)
}

let idempotencySequence = 0

/**
 * Create a key for one user-triggered execution.
 *
 * The content hash keeps diagnostics readable, while the nonce makes an
 * intentional re-run a new request. A single mutation keeps this key for its
 * one HTTP request, so transport retries remain idempotent.
 */
export function freshIdempotencyKey(scope: string, value: string): string {
  idempotencySequence = (idempotencySequence + 1) >>> 0
  const randomPart =
    globalThis.crypto?.randomUUID?.() ??
    Math.random().toString(36).slice(2)
  const nonce = `${Date.now().toString(36)}-${idempotencySequence.toString(36)}-${randomPart}`
  return `${scope}:${shortHash(value)}:${nonce}`.slice(0, 128)
}

export function estimateRange(
  bookId: string,
  input: EstimateInput,
  signal?: AbortSignal,
): Promise<EstimateOut> {
  const reason = dialogueStrategyDisabledReason(input.range.dialogueStrategy, input.budget.maxRecheckRounds ?? 0)
  if (reason) return Promise.reject(new Error(reason))
  return apiData<EstimateOut>(`/api/books/${bookId}/estimates`, {
    method: 'POST',
    signal,
    body: {
      book_version_id: input.bookVersionId ?? null,
      range: {
        chapter_id: input.range.chapterId,
        start_cp: input.range.startCp,
        end_cp: input.range.endCp,
        ...dialogueStrategyPayload(input.range.dialogueStrategy),
      },
      reading_mode: input.readingMode,
      visible_horizon_cp: input.visibleHorizonCp ?? null,
      budget: budgetPayload(input.budget),
    },
  })
}

export interface CreateJobInput {
  inferenceOptions?: InferenceOptions
  bookId: string
  mode: 'preview' | 'process'
  bookVersionId?: string | null
  range: RangeInput
  selectedWindowIds?: string[] | null
  forceReprocess?: boolean
  profileId?: string | null
  readingMode: ReadingMode
  visibleHorizonCp?: number | null
  budget: BudgetInput
  idempotencyKey: string
  runNow?: boolean
}

export function createJob(input: CreateJobInput, signal?: AbortSignal): Promise<JobDetailOut> {
  const reason = dialogueStrategyDisabledReason(input.range.dialogueStrategy, input.budget.maxRecheckRounds ?? 0)
  if (reason) return Promise.reject(new Error(reason))
  return apiData<JobDetailOut>('/api/jobs', {
    method: 'POST',
    signal,
    body: {
      book_id: input.bookId,
      kind: 'INFERENCE',
      mode: input.mode,
      book_version_id: input.bookVersionId ?? null,
      range: {
        chapter_id: input.range.chapterId,
        start_cp: input.range.startCp,
        end_cp: input.range.endCp,
        ...dialogueStrategyPayload(input.range.dialogueStrategy),
      },
      selected_window_ids: input.selectedWindowIds ?? null,
      force_reprocess: input.forceReprocess ?? false,
      profile_id: input.profileId ?? null,
      ...(input.inferenceOptions ? { inference_options: input.inferenceOptions } : {}),
      reading_mode: input.readingMode,
      visible_horizon_cp: input.visibleHorizonCp ?? null,
      budget: budgetPayload(input.budget),
      idempotency_key: input.idempotencyKey,
      run_now: input.runNow ?? true,
    },
  })
}

export function fetchUsage(bookId: string, signal?: AbortSignal): Promise<UsageOut> {
  return apiData<UsageOut>(`/api/books/${bookId}/usage`, { signal })
}
/**
 * 任务恢复：把非完成状态翻译成可执行动作。
 *
 * 只读接口；返回的 `paid` 标记该动作是否可能产生模型费用。
 */
export function fetchJobRecovery(jobId: string, signal?: AbortSignal): Promise<JobRecoveryOut> {
  return apiData<JobRecoveryOut>(`/api/jobs/${jobId}/recovery`, { signal })
}

export function pauseJob(jobId: string, signal?: AbortSignal): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/jobs/${jobId}/pause`, { method: 'POST', signal })
}

export function resumeJob(jobId: string, signal?: AbortSignal): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/jobs/${jobId}/resume`, { method: 'POST', signal })
}

export function runJobNow(jobId: string, signal?: AbortSignal): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/jobs/${jobId}/run`, { method: 'POST', signal })
}

export function reconcileJob(
  jobId: string,
  action: 'retry' | 'keep_unknown',
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return apiData<Record<string, unknown>>(`/api/jobs/${jobId}/reconcile`, {
    method: 'POST',
    body: { action },
    signal,
  })
}
