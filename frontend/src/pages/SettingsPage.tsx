import { Link } from 'react-router-dom'
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { ThinkingSettings } from '../components/ThinkingSettings'
import { DialogueStrategySettings } from '../components/DialogueStrategySettings'
import { RosterRepairSettings } from '../components/RosterRepairSettings'
import { ApplicationSettings } from '../components/ApplicationSettings'
import { ChapterFilterSettings } from '../components/ChapterFilterSettings'
import { useProcessingPreferences } from '../processing/preferences'
import { getProcessingPreferences, getDefaultProcessingPreferences } from '../processing/preferences'
import type { ProcessingPreferences } from '../processing/preferences'
import { stopAutomaticProcessing } from '../processing/autoProcessing'
import { defaultSettings, useGeneralSettings } from '../settings/preferences'
import { getGeneralSettings } from '../settings/preferences'
import type { GeneralSettings } from '../settings/preferences'

export default function SettingsPage() {
  const [storedSettings, saveSettings] = useGeneralSettings()
  const [storedPreferences, savePreferences] = useProcessingPreferences()
  const [settings, setSettings] = useState(storedSettings)
  const [preferences, setPreferences] = useState(storedPreferences)
  const [saved, setSaved] = useState(false)
  const update = (patch: Partial<GeneralSettings>) => { setSettings(previous => ({ ...previous, ...patch })); setSaved(false) }
  const updatePreferences = (patch: Partial<ProcessingPreferences>) => { setPreferences(previous => ({ ...previous, ...patch })); setSaved(false) }
  const changed = JSON.stringify(settings) !== JSON.stringify(storedSettings) || JSON.stringify(preferences) !== JSON.stringify(storedPreferences)
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal) })
  return <div className="ndr-page">
    <header className="card ndr-page-header"><h2>通用设置</h2><p className="hint">修改后请点击保存。阅读与处理偏好保存在当前浏览器；应用配置保存在本机，重启服务后生效。</p></header>
    <section className="card">
      <h3>阅读显示</h3>
      <div className="ndr-range-grid">
        <label className="ndr-field">正文字号<input type="number" min={14} max={28} value={settings.fontSize} onChange={e => update({ fontSize: Number(e.target.value) })} /></label>
        <label className="ndr-field">正文行距<input type="number" min={1.5} max={2.6} step={0.05} value={settings.lineHeight} onChange={e => update({ lineHeight: Number(e.target.value) })} /></label>
      </div>
      <div className="ndr-settings-options">
        <label><input type="checkbox" checked={settings.resumeReading} onChange={e => update({ resumeReading: e.target.checked })} />重新打开时自动续读（不影响最后阅读位置的保存）</label>
        <label><input type="checkbox" checked={settings.showCandidates} onChange={e => update({ showCandidates: e.target.checked })} />阅读页默认显示候选引语</label>
        <label><input type="checkbox" checked={settings.showAnnotations} onChange={e => update({ showAnnotations: e.target.checked })} />阅读页默认显示人物标注</label>
        <label><input type="checkbox" checked={settings.showReviewMarkers} onChange={e => update({ showReviewMarkers: e.target.checked })} />显示待确认对白标记（⚠）</label>
        <label><input type="checkbox" checked={settings.doubleClickChapterStatus} onChange={e => update({ doubleClickChapterStatus: e.target.checked })} />双击目录章节名切换完成状态</label>
      </div>
      <p className="hint">显示开关在下一次进入阅读页时作为默认值；阅读中仍可临时调整。字号和行距也用于处理页原文预览，不改变导出样式。</p>
      <p className="hint">待确认标记默认关闭；保存后立即用于阅读与处理页原文预览，为所有仍待确认的对白显示 ⚠，不改变人物归属或导出内容。</p>
      <p className="hint">双击切换默认关闭，保存开启后生效：未处理与已完成互相切换；处理中、失败或已停止先标为已完成，再次双击改为未处理。仅改完成标记，不删除标注或停止已发送的模型请求；重新启动本章处理后恢复自动进度更新。</p>
      <div className="ndr-document"><p>阅读样例：「雨停了。」少女合上伞。</p></div>
      <button onClick={() => {
        if (!window.confirm('仅恢复阅读显示的默认值？其他区域不变，点击保存后生效。')) return
        const { fontSize, lineHeight, resumeReading, showCandidates, showAnnotations, showReviewMarkers, doubleClickChapterStatus } = defaultSettings
        update({ fontSize, lineHeight, resumeReading, showCandidates, showAnnotations, showReviewMarkers, doubleClickChapterStatus })
      }}>恢复阅读显示默认值</button>
    </section>
    <section className="card">
      <h3>人物资料更新</h3>
      <label><input type="checkbox" checked={settings.allowOverwriteManualCharacters} onChange={event => update({ allowOverwriteManualCharacters: event.target.checked })} />允许后台人物识别更新人工姓名与说明</label>
      <p className="hint">默认关闭。保存开启后，批量处理及阅读时的自动处理可以根据有原文依据的人物识别结果修正人工资料；模型可能误判。仅影响之后创建的人物任务，已有任务保持原设置。单章人工确认仍由你选择。</p>
      <button onClick={() => {
        if (window.confirm('仅恢复人物资料更新的默认值（关闭后台覆盖人工资料）？其他区域不变，点击保存后生效。')) {
          update({ allowOverwriteManualCharacters: defaultSettings.allowOverwriteManualCharacters })
        }
      }}>恢复人物资料更新默认值</button>
    </section>
    <ChapterFilterSettings value={settings} onChange={update} />
    <section className="card" data-testid="automatic-processing-settings">
      <h3>自动提前处理章节</h3>
      <label><input type="checkbox" checked={settings.autoProcessing} onChange={event => {
        update({ autoProcessing: event.target.checked })
      }} />阅读时自动处理当前章及后续章节（会调用模型）</label>
      <p className="hint">默认关闭。开启后会自动识别人物并确认对白归属，可能消耗付费额度；已完成章节会跳过。请保持页面打开，可切换到应用内其他页面。</p>
      <div className="ndr-range-grid">
        <label className="ndr-field">提前处理后续章节数<input type="number" min={0} max={100} value={settings.lookAheadChapters} onChange={event => update({ lookAheadChapters: Number(event.target.value) })} /></label>
        <label className="ndr-field">最大并发任务数<input type="number" min={1} max={16} value={preferences.concurrency} onChange={event => updatePreferences({ concurrency: Number(event.target.value) })} /></label>
        <label className="ndr-field">自动处理 Token 上限（留空＝不限）<input type="number" min={1} value={preferences.tokenLimit ?? ''} onChange={event => updatePreferences({ tokenLimit: event.target.value ? Number(event.target.value) : null })} /></label>
        <label className="ndr-field">每个窗口最多复核次数（0 关闭，每轮全部对白）<input type="number" min={0} value={preferences.maxRecheckRounds} onChange={event => updatePreferences({ maxRecheckRounds: Number(event.target.value) })} /></label>
      </div>
      <ThinkingSettings disabled={false} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => updatePreferences({ profileId })} profileTestId="automatic-profile"
        draftPreferences={preferences} onPreferencesChange={updatePreferences} />
      {profiles.isError && <p className="status-error">模型配置读取失败：{profiles.error.message}<button onClick={() => void profiles.refetch()}>重新读取</button></p>}
      <DialogueStrategySettings value={preferences.dialogueStrategy} rounds={preferences.maxRecheckRounds}
        onChange={dialogueStrategy => updatePreferences({ dialogueStrategy })} />
      <p className="hint">对白策略修改后也需点击“保存阅读与处理设置”；人物合并不使用此选项。</p>
      <RosterRepairSettings preferences={preferences} onChange={updatePreferences} />
      <p className="hint">人物修复配置同样点击“保存阅读与处理设置”后生效，已启动任务保持原配置。</p>
      <p className="hint">这些模型、思考、并发和额度设置与单章、批量处理共用。自动处理额度按本页会话中每本书累计；接近上限时会提醒调整，阅读侧栏可停止或刷新额度并重试。修改设置不改变已启动的任务。</p>
      <Link className="ndr-button" to="/settings/models">管理模型账号</Link>
      <button onClick={() => {
        if (!window.confirm('恢复本栏的自动处理开关、提前章节数、模型选择、思考、对白策略、人物修复、并发、复核和Token上限默认值？不删除模型账号，其他区域及未在本栏显示的参数不变，点击保存后生效。')) return
        update({ autoProcessing: defaultSettings.autoProcessing, lookAheadChapters: defaultSettings.lookAheadChapters })
        const { profileId, concurrency, tokenLimit, maxRecheckRounds, thinkingMode, thinkingEffort, dialogueStrategy, rosterRepairEnabled, maxRosterRepairs, maxFormatRetries } = getDefaultProcessingPreferences()
        updatePreferences({ profileId, concurrency, tokenLimit, maxRecheckRounds, thinkingMode, thinkingEffort, dialogueStrategy, rosterRepairEnabled, maxRosterRepairs,
          ...(preferences.rosterRepairEnabled ? { maxFormatRetries } : {}) })
      }}>恢复自动处理默认值</button>
      <p className="hint">恢复仅修改本栏草稿，不停止任务或删除模型账号；保存后生效。输出上限不变；人物修复开启时，本栏可见的校验失败重试次数也会恢复默认值。</p>
    </section>
    <section className="card" aria-label="保存阅读与处理设置">
      <button className="ndr-primary" disabled={!changed} title={!changed ? '尚未修改阅读或处理设置。' : undefined} onClick={() => {
        if (storedSettings.autoProcessing && !settings.autoProcessing) stopAutomaticProcessing()
        saveSettings(settings); savePreferences(preferences)
        setSettings(getGeneralSettings()); setPreferences(getProcessingPreferences()); setSaved(true)
      }}>保存阅读与处理设置</button>
      <p className="hint" role="status">{changed ? '有未保存的修改，离开页面会丢弃。保存后生效，不需要重启服务。' : saved ? '阅读与处理设置已保存。' : '修改阅读或处理设置后可保存。'}</p>
    </section>
    <ApplicationSettings />
  </div>
}
