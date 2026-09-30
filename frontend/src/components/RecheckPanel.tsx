/**
 * 局部复核：**会创建真实任务并可能产生费用**。
 *
 * 与「展开更多原文」（本地只读）严格分开：这里必须显式选择模型配置、填写上限并再次确认。
 */
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { fetchProfiles, profileKeys } from '../api/profiles'
import { recheckQuote } from '../api/review'
import type { JobDetailOut } from '../api/types'
import { JobPanel } from './JobPanel'
import { ThinkingSettings } from './ThinkingSettings'
import { inferenceOptions, useProcessingPreferences } from '../processing/preferences'
import { freshIdempotencyKey } from '../api/jobs'
import { TERMINAL_JOB_STATES } from '../processing/jobCompletion'

export interface RecheckPanelProps {
  quoteId: string
  onStarted?: (job: JobDetailOut) => void
}

export function RecheckPanel({ quoteId, onStarted }: RecheckPanelProps) {
  const profiles = useQuery({
    queryKey: profileKeys.profiles(),
    queryFn: ({ signal }) => fetchProfiles(signal),
  })
  const [preferences, updatePreferences] = useProcessingPreferences()
  const [maxInputTokens, setMaxInputTokens] = useState('20000')
  const [jobId, setJobId] = useState<string | null>(null)
  const [currentJob, setCurrentJob] = useState<JobDetailOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const running = busy || Boolean(currentJob && !TERMINAL_JOB_STATES.has(currentJob.state))

  const selectedProfile = profiles.data?.find(profile => profile.id === preferences.profileId) ?? profiles.data?.[0]
  const effectiveProfileId = selectedProfile?.id ?? ''

  const start = async () => {
    if (!effectiveProfileId) {
      setError('请先在「模型配置」添加提供方；复核会产生真实调用。')
      return
    }
    const limit = maxInputTokens.trim() === '' ? null : Number(maxInputTokens)
    if (limit !== null && (!Number.isSafeInteger(limit) || limit < 1)) {
      setError('输入 Token 上限必须是正整数，或留空表示不限。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const job = await recheckQuote(quoteId, {
        profileId: effectiveProfileId,
        inferenceOptions: inferenceOptions(preferences),
        maxInputTokens: limit,
        idempotencyKey: freshIdempotencyKey('recheck', JSON.stringify({ quoteId, effectiveProfileId, limit, options: inferenceOptions(preferences) })),
        runNow: true,
      })
      setJobId(job.id)
      setCurrentJob(job)
      onStarted?.(job)
    } catch (err) {
      setError(err instanceof Error ? err.message : '复核任务创建失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ndr-recheck" data-testid="recheck-panel">
      <p className="hint">
        局部复核会围绕**本场景**重新调用模型，可能产生费用；它不改变任何人工锁定的对白。
      </p>
      <label className="ndr-field">
        模型配置
        <select
          value={effectiveProfileId}
          disabled={running}
          onChange={(event) => updatePreferences({ profileId: event.target.value })}
          data-testid="recheck-profile"
        >
          {(profiles.data ?? []).map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name} · {profile.model}
            </option>
          ))}
        </select>
      </label>
      <ThinkingSettings disabled={running} profile={selectedProfile} />
      <label className="ndr-field">
        输入 token 上限
        <input
          type="number"
          min={1}
          disabled={running}
          value={maxInputTokens}
          onChange={(event) => setMaxInputTokens(event.target.value)}
          data-testid="recheck-max-input"
        />
      </label>
      <button
        type="button"
        className="ndr-primary"
        onClick={start}
        disabled={running}
        data-testid="recheck-start"
      >
        {busy ? '正在创建任务…' : '开始局部复核（调用模型）'}
      </button>
      {currentJob && !TERMINAL_JOB_STATES.has(currentJob.state) && <p className="hint">复核任务正在运行，配置已固定；完成或停止后可调整并重新发起。</p>}
      {error && (
        <p className="status-error" data-testid="recheck-error">
          {error}
        </p>
      )}
      {jobId && <JobPanel jobId={jobId} onUpdate={setCurrentJob} />}
    </div>
  )
}
