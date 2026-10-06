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
import { ProposalDiagnostics } from './ProposalDiagnostics'
import { JOB_STATE_LABELS } from './JobPanel'
import { getProcessingPreferences, rosterRepairOptions } from '../processing/preferences'
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

interface RosterDraftJournal {
  version: number
  source?: string
  drafts: DraftCandidate[]
  pov: string | null
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
  const [draftBasis, setDraftBasis] = useState<{ key: string; source: string; version: number } | null>(null)
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
  const rosterSource = useMemo(() => roster.data ? JSON.stringify(roster.data) : null, [roster.data])
  const draftCurrent = Boolean(roster.data && draftBasis?.key === draftKey && draftBasis.source === rosterSource)
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
    if (!roster.data || !rosterSource) return
    const saved = readJournal<RosterDraftJournal>(draftKey)
    const next = (roster.data.candidates ?? []).map(draftFromCandidate)
    const restore = roster.data.status !== 'CONFIRMED' && saved?.version === roster.data.version
      && (saved.source === rosterSource
        || (saved.source === undefined && (saved.drafts.length > 0 || next.length === 0)))
    setDrafts(restore ? saved!.drafts : next)
    const suggested = (roster.data.candidates ?? []).find((item) => item.pov_candidate)
    setPovTempRef(restore ? saved!.pov : suggested?.temp_ref ?? null)
    setDraftBasis({ key: draftKey, source: rosterSource, version: roster.data.version })
    setMessage(null)
    setError(null)
  }, [roster.data, rosterSource, draftKey])

  useEffect(() => {
    if (draftCurrent && draftBasis && roster.data?.status !== 'CONFIRMED') {
      writeJournal(draftKey, { version: draftBasis.version, source: draftBasis.source, drafts, pov: povTempRef })
    }
  }, [draftKey, draftBasis, draftCurrent, drafts, povTempRef, roster.data])

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
      if (!draftCurrent || !draftBasis) throw new Error('人物名单正在更新，请等待读取完成后再确认。')
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
        expectedVersion: draftBasis.version,
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
    draftCurrent &&
    acceptedCount > 0 &&
    Boolean(povTempRef) &&
    drafts.some((item) => item.accepted && item.temp_ref === povTempRef) &&
    drafts
      .filter((item) => item.accepted)
      .every((item) => Boolean(item.character_id || item.canonical_name.trim()))

  const analyzeBlocker = disabled ? '本书处理任务正在运行，请等待结束或先停止任务。'
    : analyze.isPending ? '正在创建人物分析任务，请等待提交完成。'
    : textlessCompleted ? '本章没有正文文字，已完成，无需再分析人物。'
    : recent.isPending ? '正在读取已有的人物任务，读取完成后再分析。'
    : recent.isError ? '已有任务读取失败，请先点击“重新读取任务”。'
    : job.data && !TERMINAL_JOB_STATES.has(job.data.state) ? '人物分析尚未结束，请等待完成或先停止任务。'
    : !profileId ? '请先在上方选择模型配置，再分析本章人物。' : null
  const confirmBlocker = confirm.isPending ? '正在保存人物名单，请等待保存完成。'
    : !draftCurrent ? '人物名单正在更新，请等待读取完成后再确认。'
    : textlessCompleted ? '无文字章节无需确认人物或主人公，不必为了启用按钮而添加人物。'
    : acceptedCount === 0 ? '请先分析或手动添加人物，并勾选至少一个“确认本章出现”。'
    : !drafts.some(item => item.accepted && item.temp_ref === povTempRef) ? '请先为已勾选的人物选择“本章第一视角主人公”。'
    : !canConfirm ? '请为已勾选的人物填写姓名，或选择已有全书人物，再确认名单。' : null

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
                ...rosterRepairOptions(getProcessingPreferences()),
                maxInputTokens: getProcessingPreferences().tokenLimit,
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
            title={analyzeBlocker ?? undefined}
          >
            分析本章人物
          </button>
          <button type="button" onClick={addDraft} data-testid="roster-add-character"
            disabled={!draftCurrent}
            title={!draftCurrent ? '人物名单正在读取，请等待读取完成后再添加。' : undefined}>
            手动添加人物
          </button>
        </div>
      </div>

      {analyzeBlocker && !textlessCompleted && <p className="hint" role="status">{analyzeBlocker}</p>}

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
      {job.data?.state === 'COMPLETED' && job.data.range?.chapter_id === chapterId && (
        <ProposalDiagnostics value={job.data.progress?.proposal_diagnostics} />
      )}
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
                  title={!item.accepted ? '请先勾选该人物“确认本章出现”，再设为主人公。' : undefined}
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
          title={confirmBlocker ?? undefined}
        >
          确认人物与主人公
        </button>
      </div>
      {confirmBlocker && <p className="hint" role="status">{confirmBlocker}</p>}
    </section>
  )
}
