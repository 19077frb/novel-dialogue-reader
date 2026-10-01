import { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { queryKeys } from '../api/books'
import { ReadErrorNotice } from './ReadErrorNotice'
import { OperationTimer } from './OperationTimer'
import { BatchRetryControls, useBatchProgress } from './BatchProcessor'
import { useGeneralSettings, updateGeneralSettings } from '../settings/preferences'
import { useProcessingPreferences } from '../processing/preferences'
import { autoBusy, autoMessage, resetAutomaticProcessing, scheduleAutomaticProcessing, stopAutomaticProcessing } from '../processing/autoProcessing'

export function AutomaticProcessing({ bookId, bookVersionId, chapterId }: { bookId: string; bookVersionId?: string | null; chapterId: string | null }) {
  const [settings] = useGeneralSettings()
  const [preferences] = useProcessingPreferences()
  const client = useQueryClient()
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const progress = useBatchProgress(bookId)
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal), enabled: settings.autoProcessing })
  useEffect(() => {
    if (!settings.autoProcessing || !bookVersionId || !chapterId) return
    const tick = () => {
      if (profiles.data?.some(profile => profile.id === preferences.profileId)) {
        void scheduleAutomaticProcessing(bookId, bookVersionId, chapterId, settings.lookAheadChapters, preferences,
          () => { void client.invalidateQueries({ queryKey: queryKeys.chapters(bookId) }) })
      }
      setMessage(autoMessage(bookId)); setBusy(autoBusy(bookId))
    }
    const initial = window.setTimeout(tick, 700)
    const timer = window.setInterval(tick, 2000)
    return () => { window.clearTimeout(initial); window.clearInterval(timer) }
  }, [bookId, bookVersionId, chapterId, settings.autoProcessing, settings.lookAheadChapters, preferences, profiles.data, client])
  if (!settings.autoProcessing) return null
  return <section className="card" data-testid="automatic-processing">
    <h3>自动提前处理</h3>
    <p className="hint">处理当前章及后 {settings.lookAheadChapters} 章；已完成章节不会重复消耗额度。<Link to="/settings/general">调整设置</Link></p>
    {!preferences.profileId && <p className="hint">请先在通用设置中选择模型。</p>}
    {preferences.profileId && profiles.isSuccess && !profiles.data.some(profile => profile.id === preferences.profileId) && <p className="status-error">所选模型配置已不可用，请到通用设置重新选择。</p>}
    {profiles.isError && <ReadErrorNotice label="模型配置读取失败" error={profiles.error} retrying={profiles.isFetching} onRetry={() => void profiles.refetch()} />}
    <p role="status">{message || '等待空闲任务位置…'}</p>
    <OperationTimer startedAt={progress.startedAt} finishedAt={progress.finishedAt}
      completed={progress.tasks.filter(task => task.state === 'completed').length} total={progress.tasks.length} />
    <BatchRetryControls bookId={bookId} />
    <button className="ndr-danger" onClick={() => { stopAutomaticProcessing(); updateGeneralSettings({ autoProcessing: false }) }}>停止自动处理</button>
    <button disabled={busy} onClick={() => { resetAutomaticProcessing(bookId); setMessage('已刷新额度和重试记录，等待自动处理。') }}>刷新额度并重试</button>
    {busy && <p className="hint">刷新额度前请等待当前自动处理结束，或先停止处理。</p>}
  </section>
}
