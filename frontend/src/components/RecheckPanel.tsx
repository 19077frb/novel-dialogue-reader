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

export interface RecheckPanelProps {
  quoteId: string
  onStarted?: (job: JobDetailOut) => void
}

export function RecheckPanel({ quoteId, onStarted }: RecheckPanelProps) {
  const profiles = useQuery({
    queryKey: profileKeys.profiles(),
    queryFn: ({ signal }) => fetchProfiles(signal),
  })
  const [profileId, setProfileId] = useState('')
  const [maxInputTokens, setMaxInputTokens] = useState('20000')
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const effectiveProfileId = profileId || profiles.data?.[0]?.id || ''

  const start = async () => {
    if (!effectiveProfileId) {
      setError('请先在「模型配置」添加提供方；复核会产生真实调用。')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const job = await recheckQuote(quoteId, {
        profileId: effectiveProfileId,
        maxInputTokens: maxInputTokens.trim() === '' ? null : Number(maxInputTokens),
        idempotencyKey: `recheck:${quoteId}:${effectiveProfileId}:${maxInputTokens}`,
        runNow: true,
      })
      setJobId(job.id)
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
      <label>
        模型配置
        <select
          value={effectiveProfileId}
          onChange={(event) => setProfileId(event.target.value)}
          data-testid="recheck-profile"
        >
          {(profiles.data ?? []).map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.name} · {profile.model}
            </option>
          ))}
        </select>
      </label>
      <label>
        输入 token 上限
        <input
          type="number"
          min={1}
          value={maxInputTokens}
          onChange={(event) => setMaxInputTokens(event.target.value)}
          data-testid="recheck-max-input"
        />
      </label>
      <button
        type="button"
        onClick={start}
        disabled={busy}
        data-testid="recheck-start"
      >
        {busy ? '正在创建任务…' : '开始局部复核（调用模型）'}
      </button>
      {error && (
        <p className="status-error" data-testid="recheck-error">
          {error}
        </p>
      )}
      {jobId && <JobPanel jobId={jobId} />}
    </div>
  )
}