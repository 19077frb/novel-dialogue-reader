/**
 * 局部复核：**会创建真实任务并可能产生费用**。
 *
 * 与「展开更多原文」（本地只读）严格分开：这里必须显式选择模型配置、填写上限并再次确认。
 */
import { useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { fetchProfiles, profileKeys } from '../api/profiles'
import { recheckQuote } from '../api/review'
import type { JobDetailOut } from '../api/types'
import { JobPanel } from './JobPanel'
import { ThinkingSettings } from './ThinkingSettings'
import { DialogueStrategySettings } from './DialogueStrategySettings'
import { FormatRetrySetting } from './FormatRetrySetting'
import { inferenceOptions, useProcessingPreferences } from '../processing/preferences'
import { freshIdempotencyKey, fetchRecentJobs, dialogueStrategyDisabledReason } from '../api/jobs'
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
  const recent = useQuery({ queryKey: ['recent-recheck', quoteId],
    queryFn: ({ signal }) => fetchRecentJobs({ quoteId, kind: 'RECHECK', limit: 1 }, signal) })
  useEffect(() => {
    const restored = recent.data?.[0]
    setJobId(restored?.id ?? null)
    setCurrentJob(restored ?? null)
  }, [quoteId, recent.data])
  const running = busy || recent.isPending || recent.isError || Boolean(currentJob && !TERMINAL_JOB_STATES.has(currentJob.state))

  const selectedProfile = profiles.data?.find(profile => profile.id === preferences.profileId) ?? profiles.data?.[0]
  const effectiveProfileId = selectedProfile?.id ?? ''
  const strategyReason = dialogueStrategyDisabledReason(preferences.dialogueStrategy, preferences.maxRecheckRounds)

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
        dialogueStrategy: preferences.dialogueStrategy,
        inferenceOptions: inferenceOptions(preferences),
        maxInputTokens: limit,
        maxRecheckRounds: preferences.maxRecheckRounds,
        maxFormatRetries: preferences.maxFormatRetries,
        idempotencyKey: freshIdempotencyKey('recheck', JSON.stringify({ quoteId, effectiveProfileId, limit, options: inferenceOptions(preferences) })),
        runNow: true,
      })
      setJobId(job.id)
      setCurrentJob(job)
      onStarted?.(job)
    } catch (err) {
      setError(err instanceof Error ? err.message : '复核任务创建失败')
      void recent.refetch()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ndr-recheck" data-testid="recheck-panel">
      <p className="hint">
        局部复核会围绕**本场景**重新调用模型，可能产生费用；它不改变任何人工锁定的对白。
      </p>
      {recent.isPending && <p className="hint">正在读取已有复核任务…</p>}
      <ThinkingSettings disabled={running} profiles={profiles.data ?? []} profileId={effectiveProfileId}
        disabledReason={recent.isError ? '已有复核任务读取失败，请先点击“重新读取任务”。' : recent.isPending ? '正在读取已有复核任务，请等待读取完成。' : '局部复核正在提交或执行，请等待完成或先停止任务再调整配置。'}
        onProfileChange={profileId => updatePreferences({ profileId })} profileTestId="recheck-profile" />
      <DialogueStrategySettings value={preferences.dialogueStrategy} rounds={preferences.maxRecheckRounds} disabled={running}
        disabledReason={recent.isError ? '已有复核任务读取失败，请先重新读取任务。' : recent.isPending ? '正在读取已有复核任务，请等待完成。' : undefined}
        onChange={dialogueStrategy => updatePreferences({ dialogueStrategy })} />
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
      <FormatRetrySetting value={preferences.maxFormatRetries} disabled={running}
        onChange={maxFormatRetries => updatePreferences({ maxFormatRetries })} testId="recheck-format-retries" />
      <label className="ndr-field">
        每个窗口最多复核次数
        <input type="number" min={0} disabled={running} value={preferences.maxRecheckRounds}
          onChange={event => updatePreferences({ maxRecheckRounds: Math.max(0, Math.trunc(Number(event.target.value) || 0)) })}
          title={running ? '请等待现有任务读取或完成后再调整复核次数。' : undefined} />
        <span className="hint">首次处理后最多进行这些轮复核；0 关闭，每轮检查本次范围全部对白，并额外消耗 Tokens。</span>
      </label>
      <button
        type="button"
        className="ndr-primary"
        onClick={start}
        disabled={running || Boolean(strategyReason)}
        data-testid="recheck-start"
        title={running ? '请先等待现有任务状态读取或复核完成；读取失败时点击“重新读取任务”。' : strategyReason ?? undefined}
      >
        {busy ? '正在创建任务…' : '开始局部复核（调用模型）'}
      </button>
      {currentJob && !TERMINAL_JOB_STATES.has(currentJob.state) && <p className="hint">复核任务正在运行，配置已固定；完成或停止后可调整并重新发起。</p>}
      {error && (
        <p className="status-error" data-testid="recheck-error">
          {error}
        </p>
      )}
      {recent.isError && <p role="alert" className="status-error">已有复核任务读取失败：{recent.error.message} <button onClick={() => void recent.refetch()}>重新读取任务</button></p>}
      {jobId && <JobPanel jobId={jobId} onUpdate={setCurrentJob} />}
    </div>
  )
}
