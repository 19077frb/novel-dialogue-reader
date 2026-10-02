/**
 * 章节人物名单：模型先识别人物，用户确认后再进行对白归属。
 */
import { apiData } from './client'
import type {
  BookCharacterOut,
  InferenceOptions,
  CharacterDirectoryOut,
  CharacterEditIn,
  CharacterMergeIn,
  CharacterAutoMergeIn,
  CharacterAutoMergeResultOut,
  ChapterRosterOut,
  JobDetailOut,
  RosterConfirmCandidateIn,
  RosterConfirmIn,
} from './types'

export function fetchCharacterDirectory(bookId: string, signal?: AbortSignal) {
  return apiData<CharacterDirectoryOut[]>(`/api/books/${bookId}/character-directory`, { signal })
}

export function confirmCharacterAutoMerge(bookId: string, jobId: string, selectedTargetIds: string[]) {
  return apiData<CharacterAutoMergeResultOut>(`/api/books/${bookId}/character-directory/auto-merge/${jobId}/confirm`,
    { method: 'POST', body: { selected_target_ids: selectedTargetIds } })
}

export function editBookCharacter(bookId: string, entryId: string, body: CharacterEditIn) {
  return apiData<CharacterDirectoryOut>(
    `/api/books/${bookId}/character-directory/${encodeURIComponent(entryId)}`, { method: 'PUT', body },
  )
}

export function mergeBookCharacter(bookId: string, entryId: string, body: CharacterMergeIn) {
  return apiData<CharacterDirectoryOut>(
    `/api/books/${bookId}/character-directory/${encodeURIComponent(entryId)}/merge`, { method: 'POST', body },
  )
}

export const characterKeys = {
  book: (bookId: string, bookVersionId: string | null | undefined) =>
    ['book-characters', bookId, bookVersionId ?? 'active'] as const,
  roster: (
    bookId: string,
    bookVersionId: string | null | undefined,
    chapterId: string | null,
  ) => ['character-roster', bookId, bookVersionId ?? 'active', chapterId] as const,
  rosterJob: (jobId: string | null) => ['character-roster-job', jobId] as const,
}

export function fetchBookCharacters(
  bookId: string,
  bookVersionId: string | null | undefined,
  signal?: AbortSignal,
): Promise<BookCharacterOut[]> {
  const version = bookVersionId ? `?book_version_id=${encodeURIComponent(bookVersionId)}` : ''
  return apiData<BookCharacterOut[]>(`/api/books/${bookId}/characters${version}`, { signal })
}

export interface AnalyzeRosterInput {
  inferenceOptions?: InferenceOptions
  bookVersionId: string | null | undefined
  profileId: string
  idempotencyKey: string
  maxInputTokens?: number | null
  runNow?: boolean
}

export function analyzeCharacterRoster(
  bookId: string,
  chapterId: string,
  input: AnalyzeRosterInput,
  signal?: AbortSignal,
): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(
    `/api/books/${bookId}/chapters/${chapterId}/character-roster/analyze`,
    {
      method: 'POST',
      signal,
      body: {
        book_version_id: input.bookVersionId ?? null,
        profile_id: input.profileId,
        ...(input.inferenceOptions ? { inference_options: input.inferenceOptions } : {}),
        idempotency_key: input.idempotencyKey,
        max_input_tokens: input.maxInputTokens ?? null,
        run_now: input.runNow ?? true,
      },
    },
  )
}

export function fetchCharacterRoster(
  bookId: string,
  chapterId: string,
  bookVersionId: string | null | undefined,
  signal?: AbortSignal,
): Promise<ChapterRosterOut> {
  const version = bookVersionId ? `?book_version_id=${encodeURIComponent(bookVersionId)}` : ''
  return apiData<ChapterRosterOut>(
    `/api/books/${bookId}/chapters/${chapterId}/character-roster${version}`,
    { signal },
  )
}

export interface ConfirmRosterInput {
  bookVersionId: string | null | undefined
  candidates: RosterConfirmCandidateIn[]
  povTempRef: string | null
  expectedVersion: number
}

export function confirmCharacterRoster(
  bookId: string,
  chapterId: string,
  input: ConfirmRosterInput,
  signal?: AbortSignal,
): Promise<ChapterRosterOut> {
  const payload: RosterConfirmIn = {
    book_version_id: input.bookVersionId ?? null,
    candidates: input.candidates,
    pov_temp_ref: input.povTempRef,
    expected_version: input.expectedVersion,
  }
  return apiData<ChapterRosterOut>(
    `/api/books/${bookId}/chapters/${chapterId}/character-roster`,
    {
      method: 'PUT',
      signal,
      body: payload,
    },
  )
}

export function startCharacterAutoMerge(bookId: string, input: CharacterAutoMergeIn): Promise<JobDetailOut> {
  return apiData<JobDetailOut>(`/api/books/${bookId}/character-directory/auto-merge`, { method: 'POST', body: input })
}

export function fetchCharacterAutoMergeResult(bookId: string, jobId: string, signal?: AbortSignal): Promise<CharacterAutoMergeResultOut> {
  return apiData<CharacterAutoMergeResultOut>(`/api/books/${bookId}/character-directory/auto-merge/${jobId}`, { signal })
}

export function fetchLatestCharacterAutoMerge(bookId: string, versionId: string, signal?: AbortSignal): Promise<CharacterAutoMergeResultOut | null> {
  return apiData<CharacterAutoMergeResultOut | null>(`/api/books/${bookId}/character-directory/auto-merge?book_version_id=${encodeURIComponent(versionId)}`, { signal })
}
