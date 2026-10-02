import { useEffect, useMemo, useState } from 'react'
import { CollapsibleBlock } from './CollapsibleBlock'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  analyzeCharacterRoster,
  characterKeys,
  confirmCharacterRoster,
  fetchBookCharacters,
  fetchCharacterRoster,
  type AnalyzeRosterInput,
} from '../api/characters'
import { fetchJob } from '../api/books'
import { OperationTimer } from './OperationTimer'
import { ReadErrorNotice } from './ReadErrorNotice'
import { JOB_STATE_LABELS } from './JobPanel'
import { readJournal, writeJournal, removeJournal } from '../processing/journal'
import { freshIdempotencyKey, fetchRecentJobs } from '../api/jobs'
import type {
  JobDetailOut,
  InferenceOptions,
  RosterCharacterCandidate,
  RosterConfirmCandidateIn,
} from '../api/types'

interface CharacterRosterPanelProps {
  /** 在分步流程中的步骤编号（可选，仅用于展示）。 */
  step?: number
  bookId: string
  bookVersionId: string | null | undefined
  chapterId: string | null
  profileId: string
  inferenceOptions?: InferenceOptions
  disabled?: boolean
  onConfirmedChange: (confirmed: boolean) => void
}

interface DraftCandidate {
  temp_ref: string
  accepted: boolean
  character_id: string | null
  canonical_name: string
  aliasesText: string
  description: string
  evidence_refs: string[]
  pov_candidate: boolean
}

const TERMINAL_JOB_STATES = new Set<JobDetailOut['state']>([
  'COMPLETED',
  'FAILED',
  'BUDGET_EXHAUSTED',
  'PAUSED',
  'NEEDS_RECONCILIATION',
  'PARTIAL',
])

function draftFromCandidate(item: RosterCharacterCandidate): DraftCandidate {
  return {
    temp_ref: item.temp_ref,
    accepted: true,
    character_id: item.character_id ?? null,
    canonical_name: item.canonical_name ?? '',
    aliasesText: (item.aliases ?? []).join('、'),
    description: item.description,
    evidence_refs: item.evidence_refs ?? [],
    pov_candidate: item.pov_candidate,
  }
}

export function CharacterRosterPanel({
  step,
  bookId,
  bookVersionId,
  chapterId,
  profileId,
  inferenceOptions,
  disabled = false,
  onConfirmedChange,
}: CharacterRosterPanelProps) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<DraftCandidate[]>([])
  const [povTempRef, setPovTempRef] = useState<string | null>(null)
  const [rosterJobId, setRosterJobId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [draftVersion, setDraftVersion] = useState<number | null>(null)
  const draftKey = `roster-draft:${bookId}:${bookVersionId}:${chapterId}`

  const rosterKey = useMemo(
    () => characterKeys.roster(bookId, bookVersionId, chapterId),
    [bookId, bookVersionId, chapterId],
  )
  const roster = useQuery({
    queryKey: rosterKey,
    queryFn: ({ signal }) =>
      fetchCharacterRoster(bookId, chapterId as string, bookVersionId, signal),
    enabled: Boolean(chapterId),
  })
  const characters = useQuery({
    queryKey: characterKeys.book(bookId, bookVersionId),
    queryFn: ({ signal }) => fetchBookCharacters(bookId, bookVersionId, signal),
    enabled: Boolean(chapterId),
  })
  const recent = useQuery({
    queryKey: ['recent-roster-job', bookId, bookVersionId, chapterId],
    queryFn: ({ signal }) => fetchRecentJobs({ bookId, versionId: bookVersionId ?? undefined,
      chapterId: chapterId!, kind: 'CHARACTER_ROSTER', limit: 1 }, signal),
    enabled: Boolean(chapterId),
  })
  useEffect(() => { setRosterJobId(recent.data?.[0]?.id ?? null) }, [chapterId, recent.data])
  const job = useQuery({
    queryKey: characterKeys.rosterJob(rosterJobId),
    queryFn: ({ signal }) => fetchJob(rosterJobId as string, signal),
    enabled: Boolean(rosterJobId),
    refetchInterval: (query) => {
      const data = query.state.data as JobDetailOut | undefined
      return !data || TERMINAL_JOB_STATES.has(data.state) ? false : 1500
    },
  })

  useEffect(() => {
    if (!roster.data) return
    const saved = readJournal<{ version: number; drafts: DraftCandidate[]; pov: string | null }>(draftKey)
    const next = (roster.data.candidates ?? []).map(draftFromCandidate)
    setDrafts(saved?.version === roster.data.version ? saved.drafts : next)
    const suggested = (roster.data.candidates ?? []).find((item) => item.pov_candidate)
    setPovTempRef(saved?.version === roster.data.version ? saved.pov : suggested?.temp_ref ?? null)
    setDraftVersion(roster.data.version)
    setMessage(null)
    setError(null)
  }, [roster.data, draftKey])

  useEffect(() => {
    if (roster.data && draftVersion === roster.data.version && roster.data.status !== 'CONFIRMED') {
      writeJournal(draftKey, { version: draftVersion, drafts, pov: povTempRef })
    }
  }, [draftKey, draftVersion, drafts, povTempRef, roster.data])

  useEffect(() => {
    onConfirmedChange(roster.data?.status === 'CONFIRMED')
  }, [onConfirmedChange, roster.data?.status])

  useEffect(() => {
    if (!rosterJobId || !job.data || !TERMINAL_JOB_STATES.has(job.data.state)) return
    void queryClient.invalidateQueries({ queryKey: rosterKey })
    if (job.data.progress?.skipped_reason === 'no_text') {
      void queryClient.invalidateQueries({ queryKey: ['chapters', bookId] })
    }
  }, [bookId, job.data, queryClient, rosterJobId, rosterKey])

  const analyze = useMutation({
    mutationFn: (input: AnalyzeRosterInput) =>
      analyzeCharacterRoster(bookId, chapterId as string, input),
    onSuccess: (detail) => {
      setRosterJobId(detail.id)
      setError(null)
      setMessage('正在分析本章人物，请稍候…')
    },
    onError: (err: unknown) => {
      setError(err instanceof Error ? err.message : '人物分析任务创建失败')
      void recent.refetch()
    },
  })

  const confirm = useMutation({
    mutationFn: () => {
      const candidates: RosterConfirmCandidateIn[] = drafts.map((item) => ({
        temp_ref: item.temp_ref,
        accepted: item.accepted,
        character_id: item.character_id ?? null,
        canonical_name: item.canonical_name || null,
        aliases: item.aliasesText
          .split(/[、，]/)
          .map((value) => value.trim())
          .filter(Boolean),
        description: item.description,
      }))
      return confirmCharacterRoster(bookId, chapterId as string, {
        bookVersionId,
        candidates,
        povTempRef,
        expectedVersion: roster.data?.version ?? 1,
      })
    },
    onSuccess: () => {
      removeJournal(draftKey)
      setMessage('人物与本章主人公已确认，可以开始对白归属。')
      setError(null)
      void queryClient.invalidateQueries({ queryKey: rosterKey })
      void queryClient.invalidateQueries({
        queryKey: characterKeys.book(bookId, bookVersionId),
      })
    },
    onError: (err: unknown) =>
      setError(err instanceof Error ? err.message : '人物确认失败'),
  })

  const acceptedCount = useMemo(
    () => drafts.filter((item) => item.accepted).length,
    [drafts],
  )
  const textlessCompleted = roster.data?.status === 'CONFIRMED'
    && !roster.data.pov_character_id && (roster.data.confirmed_characters ?? []).length === 0
    && (roster.data.candidates ?? []).length === 0
  const canConfirm =
    acceptedCount > 0 &&
    Boolean(povTempRef) &&
    drafts.some((item) => item.accepted && item.temp_ref === povTempRef) &&
    drafts
      .filter((item) => item.accepted)
      .every((item) => Boolean(item.character_id || item.canonical_name.trim()))

  const updateDraft = (tempRef: string, patch: Partial<DraftCandidate>) => {
    setDrafts((current) =>
      current.map((item) => (item.temp_ref === tempRef ? { ...item, ...patch } : item)),
    )
  }

  const addDraft = () => {
    setDrafts((current) => [
      ...current,
      {
        temp_ref: `user-${Date.now().toString(36)}`,
        accepted: true,
        character_id: null,
        canonical_name: '',
        aliasesText: '',
        description: '',
        evidence_refs: [],
        pov_candidate: false,
      },
    ])
  }

  if (!chapterId) {
    return (
      <section className="card" data-testid="character-roster-panel">
        <div className="ndr-step-heading">
          {typeof step === 'number' && (
            <span className="ndr-step-badge" aria-hidden="true">
              {step}
            </span>
          )}
          <div>
            <h3>本章人物确认</h3>
            <p className="hint" data-testid="roster-no-chapter">
              当前不是单章范围：整本或自定义文字范围不使用「本章人物」名单，可直接进行对白归属。
              需要确认人物与本章主人公时，请先在处理范围里选择具体章节。
            </p>
          </div>
        </div>
      </section>
    )
  }

  return (
    <section className="card" data-testid="character-roster-panel">
      <div className="ndr-preview-viewbar ndr-step-heading">
        {typeof step === 'number' && (
          <span className="ndr-step-badge" aria-hidden="true">
            {step}
          </span>
        )}
        <div>
          <h3>本章人物确认</h3>
          <p className="hint">
            对白归属的前置步骤：先让模型识别人物，确认名单并选择本章第一视角主人公。
          </p>
        </div>
        <div className="ndr-form-actions">
          <button
            type="button"
            onClick={() => {
              if (!profileId) {
                setError('请先选择模型配置。')
                return
              }
              analyze.mutate({
                inferenceOptions,
                bookVersionId,
                profileId,
                idempotencyKey: freshIdempotencyKey(
                  `roster:${bookId}:${chapterId}`,
                  JSON.stringify({ bookVersionId, profileId, chapterId }),
                ),
              })
            }}
            disabled={disabled || profileId === '' || analyze.isPending || textlessCompleted || recent.isPending || recent.isError || Boolean(job.data && !TERMINAL_JOB_STATES.has(job.data.state))}
            data-testid="roster-analyze"
          >
            分析本章人物
          </button>
          <button type="button" onClick={addDraft} data-testid="roster-add-character">
            手动添加人物
          </button>
        </div>
      </div>

      {textlessCompleted && <p className="hint">本章没有正文文字，已完成，无需人物识别或对白处理，不消耗模型 Token。</p>}
      {roster.isPending && (
        <p className="hint" data-testid="roster-loading">
          正在读取人物名单…
        </p>
      )}
      {roster.isError && <p className="status-error">人物名单读取失败。</p>}
      {recent.isPending && <p className="hint">正在找回人物识别任务…</p>}
      {recent.isError && <p role="alert" className="status-error">人物任务读取失败：{recent.error.message} <button onClick={() => void recent.refetch()}>重新读取任务</button></p>}
      {error && (
        <p className="status-error" data-testid="roster-error">
          {error}
        </p>
      )}
      {message && (
        <p className="hint" data-testid="roster-message">
          {message}
        </p>
      )}
      {rosterJobId && job.data && !TERMINAL_JOB_STATES.has(job.data.state) && (
        <p className="hint">人物分析任务：{JOB_STATE_LABELS[job.data.state] ?? job.data.state}</p>
      )}
      {rosterJobId && job.data && <OperationTimer startedAt={Date.parse(job.data.created_at)}
        finishedAt={TERMINAL_JOB_STATES.has(job.data.state) ? Date.parse(job.data.updated_at) : null} />}
      {rosterJobId && job.isError && <ReadErrorNotice label="人物任务读取失败" error={job.error} retrying={job.isFetching} onRetry={() => void job.refetch()} />}
      {rosterJobId && job.data?.state === 'FAILED' && (
        <p className="status-error">人物分析失败：{job.data.last_error ?? '未知错误'}</p>
      )}

      {roster.data?.status === 'CONFIRMED' && !textlessCompleted && (
        <p className="hint" data-testid="roster-confirmed">
          已确认本章主人公；如需修改，请重新确认。
        </p>
      )}

      {drafts.length > 0 ? (
        <CollapsibleBlock title="本章人物名单" summary={`共 ${drafts.length} 个人物；已选 ${drafts.filter(item => item.accepted).length} 个`}>
        <div className="ndr-character-list" data-testid="roster-candidates">
          {drafts.map((item) => (
            <article key={item.temp_ref} className="ndr-character-card">
              <label className="ndr-radio-row">
                <input
                  type="checkbox"
                  checked={item.accepted}
                  onChange={(event) =>
                    updateDraft(item.temp_ref, { accepted: event.target.checked })
                  }
                />
                <span>确认本章出现</span>
              </label>
              <label>
                姓名
                <input
                  value={item.canonical_name}
                  onChange={(event) =>
                    updateDraft(item.temp_ref, { canonical_name: event.target.value })
                  }
                  data-testid={`roster-name-${item.temp_ref}`}
                />
              </label>
              <label>
                别名（用、分隔）
                <input
                  value={item.aliasesText}
                  onChange={(event) =>
                    updateDraft(item.temp_ref, { aliasesText: event.target.value })
                  }
                />
              </label>
              <label>
                说明
                <textarea
                  value={item.description}
                  onChange={(event) =>
                    updateDraft(item.temp_ref, { description: event.target.value })
                  }
                  rows={2}
                />
              </label>
              <label>
                合并到全书人物
                <select
                  value={item.character_id ?? ''}
                  onChange={(event) => {
                    const character = (characters.data ?? []).find(
                      (candidate) => candidate.character_id === event.target.value,
                    )
                    updateDraft(item.temp_ref, {
                      character_id: event.target.value || null,
                      canonical_name: character ? character.name : item.canonical_name,
                      aliasesText: character ? (character.aliases ?? []).join('、') : item.aliasesText,
                    })
                  }}
                >
                  <option value="">（新建 / 保持本章人物）</option>
                  {(characters.data ?? []).map((character) => (
                    <option key={character.character_id} value={character.character_id}>
                      {character.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="ndr-radio-row">
                <input
                  type="radio"
                  name={`pov-${chapterId}`}
                  checked={povTempRef === item.temp_ref}
                  onChange={() => setPovTempRef(item.temp_ref)}
                  disabled={!item.accepted}
                />
                <span>本章第一视角主人公</span>
              </label>
              {item.evidence_refs.length > 0 && (
                <p className="hint">证据：{item.evidence_refs.join('、')}</p>
              )}
            </article>
          ))}
        </div>
        </CollapsibleBlock>
      ) : (
        !textlessCompleted && <p className="hint">还没有人物候选。可以先分析，也可以直接手动添加。</p>
      )}

      <div className="ndr-form-actions">
        <button
          type="button"
          className="ndr-primary"
          onClick={() => confirm.mutate()}
          disabled={!canConfirm || confirm.isPending}
          data-testid="roster-confirm"
        >
          确认人物与主人公
        </button>
      </div>
    </section>
  )
}
