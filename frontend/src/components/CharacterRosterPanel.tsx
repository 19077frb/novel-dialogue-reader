import { useEffect, useMemo, useState } from 'react'
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
import { freshIdempotencyKey } from '../api/jobs'
import type {
  JobDetailOut,
  RosterCharacterCandidate,
  RosterConfirmCandidateIn,
} from '../api/types'

interface CharacterRosterPanelProps {
  bookId: string
  bookVersionId: string | null | undefined
  chapterId: string | null
  profileId: string
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
  bookId,
  bookVersionId,
  chapterId,
  profileId,
  disabled = false,
  onConfirmedChange,
}: CharacterRosterPanelProps) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<DraftCandidate[]>([])
  const [povTempRef, setPovTempRef] = useState<string | null>(null)
  const [rosterJobId, setRosterJobId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rosterKey = characterKeys.roster(bookId, bookVersionId, chapterId)
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
    const next = (roster.data.candidates ?? []).map(draftFromCandidate)
    setDrafts(next)
    const suggested = (roster.data.candidates ?? []).find((item) => item.pov_candidate)
    setPovTempRef(suggested?.temp_ref ?? null)
    setMessage(null)
    setError(null)
  }, [roster.data])

  useEffect(() => {
    onConfirmedChange(roster.data?.status === 'CONFIRMED')
  }, [onConfirmedChange, roster.data?.status])

  useEffect(() => {
    if (!rosterJobId || !job.data || !TERMINAL_JOB_STATES.has(job.data.state)) return
    void queryClient.invalidateQueries({ queryKey: rosterKey })
  }, [job.data, queryClient, rosterJobId, rosterKey])

  const analyze = useMutation({
    mutationFn: (input: AnalyzeRosterInput) =>
      analyzeCharacterRoster(bookId, chapterId as string, input),
    onSuccess: (detail) => {
      setRosterJobId(detail.id)
      setError(null)
      setMessage('正在分析本章人物，请稍候…')
    },
    onError: (err: unknown) =>
      setError(err instanceof Error ? err.message : '人物分析任务创建失败'),
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
    return <p className="hint">请先选择章节，再识别人物。</p>
  }

  return (
    <section className="card" data-testid="character-roster-panel">
      <div className="ndr-preview-viewbar">
        <div>
          <h3>本章人物</h3>
          <p className="hint">
            先让模型识别人物，确认名单并选择本章第一视角主人公后，才会开始逐句归属。
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
                bookVersionId,
                profileId,
                idempotencyKey: freshIdempotencyKey(
                  `roster:${bookId}:${chapterId}`,
                  JSON.stringify({ bookVersionId, profileId, chapterId }),
                ),
              })
            }}
            disabled={disabled || profileId === '' || analyze.isPending}
            data-testid="roster-analyze"
          >
            分析本章人物
          </button>
          <button type="button" onClick={addDraft} data-testid="roster-add-character">
            手动添加人物
          </button>
        </div>
      </div>

      {roster.isPending && <p className="hint">正在读取人物名单…</p>}
      {roster.isError && <p className="status-error">人物名单读取失败。</p>}
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
        <p className="hint">人物分析任务进行中：{job.data.state}</p>
      )}
      {rosterJobId && job.data?.state === 'FAILED' && (
        <p className="status-error">人物分析失败：{job.data.last_error ?? '未知错误'}</p>
      )}

      {roster.data?.status === 'CONFIRMED' && (
        <p className="hint" data-testid="roster-confirmed">
          已确认本章主人公；如需修改，请重新确认。
        </p>
      )}

      {drafts.length > 0 ? (
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
      ) : (
        <p className="hint">还没有人物候选。可以先分析，也可以直接手动添加。</p>
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
