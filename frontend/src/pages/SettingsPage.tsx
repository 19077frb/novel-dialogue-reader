import { Link, useLocation } from 'react-router-dom'
import { useCallback, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { ThinkingSettings } from '../components/ThinkingSettings'
import { DialogueStrategySettings } from '../components/DialogueStrategySettings'
import { RosterRepairSettings } from '../components/RosterRepairSettings'
import { ApplicationSettings } from '../components/ApplicationSettings'
import type { ApplicationSettingsHandle, ApplicationSettingsState } from '../components/ApplicationSettings'
import '../styles/settings.css'
import { ChapterFilterSettings } from '../components/ChapterFilterSettings'
import { useStoredProcessingPreferences, getStoredProcessingPreferences, getDefaultProcessingPreferences } from '../processing/preferences'
import type { ProcessingPreferences } from '../processing/preferences'
import { stopAutomaticProcessing } from '../processing/autoProcessing'
import { defaultSettings, useGeneralSettings } from '../settings/preferences'
import { getGeneralSettings } from '../settings/preferences'
import type { GeneralSettings } from '../settings/preferences'

const categories = [
  { id: 'reading-settings', title: '阅读显示', description: '调整正文的阅读体验' },
  { id: 'character-settings', title: '人物资料', description: '设置人物资料的更新方式' },
  { id: 'chapter-filter-settings', title: '章节过滤', description: '筛选需要处理的章节' },
  { id: 'automatic-settings', title: '自动处理', description: '设置自动处理与模型偏好' },
  { id: 'experimental-settings', title: '试验功能', description: '体验探索中的阅读与处理功能' },
  { id: 'application-settings', title: '应用配置', description: '调整本机服务的运行方式' },
] as const

export default function SettingsPage() {
  const { hash } = useLocation()
  const selected = categories.find(category => `#${category.id}` === hash)?.id ?? 'reading-settings'
  const [describedCategory, setDescribedCategory] = useState<string | null>(null)
  const [storedSettings, saveSettings] = useGeneralSettings()
  const [storedPreferences, savePreferences] = useStoredProcessingPreferences()
  const [settings, setSettings] = useState(storedSettings)
  const [preferences, setPreferences] = useState(storedPreferences)
  const [saved, setSaved] = useState(false)
  const [savedApplication, setSavedApplication] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const application = useRef<ApplicationSettingsHandle>(null)
  const [applicationState, setApplicationState] = useState<ApplicationSettingsState>({
    changed: false, busy: false, ready: false, restarting: false, restartAddress: '', restartBlockedReason: null,
  })
  const applicationStateChanged = useCallback((state: ApplicationSettingsState) => {
    setApplicationState(state)
    if (state.changed) setSaved(false)
  }, [])
  const update = (patch: Partial<GeneralSettings>) => { setSettings(previous => ({ ...previous, ...patch })); setSaved(false) }
  const updatePreferences = (patch: Partial<ProcessingPreferences>) => { setPreferences(previous => ({ ...previous, ...patch })); setSaved(false) }
  const changed = JSON.stringify(settings) !== JSON.stringify(storedSettings) || JSON.stringify(preferences) !== JSON.stringify(storedPreferences)
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal) })
  const busy = saving || applicationState.busy
  const anyChanged = changed || applicationState.changed
  const restartReason = busy ? '正在保存或重启，请等待完成。' : !applicationState.ready
    ? '请先读取应用配置，再重启服务。' : applicationState.restartBlockedReason
  async function saveAll(restart = false) {
    setSaving(true); setSaveError('')
    try {
      if (applicationState.changed || restart) {
        if (!application.current) throw new Error('应用配置尚未就绪，请重新读取后保存。')
        await application.current.save(restart)
      }
      if (changed) {
        if (storedSettings.autoProcessing && !settings.autoProcessing) stopAutomaticProcessing()
        saveSettings(settings)
        savePreferences(preferences)
        setSettings(getGeneralSettings()); setPreferences(getStoredProcessingPreferences())
      }
      setSavedApplication(applicationState.changed || restart)
      setSaved(true)
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : '保存失败，请重试。')
    } finally { setSaving(false) }
  }
  return <div className="ndr-page ndr-settings" data-section={selected}>
    <header className="card ndr-page-header"><div><h2>通用设置</h2><p className="hint">选择一类设置，调整后保存。</p></div></header>
    <div className="ndr-settings-layout">
    <div className="ndr-settings-navigation" data-testid="settings-navigation">
      <nav className="ndr-section-nav" aria-label="通用设置分区">
        {categories.map(category => <Link key={category.id} to={`#${category.id}`} aria-label={category.title}
          aria-current={selected === category.id ? 'page' : undefined} aria-describedby={`${category.id}-description`}
          onMouseEnter={() => setDescribedCategory(category.id)} onMouseLeave={() => setDescribedCategory(null)}
          onFocus={() => setDescribedCategory(category.id)} onBlur={() => setDescribedCategory(null)}
          onKeyDown={event => { if (event.key === 'Escape') setDescribedCategory(null) }}>
          <span>{category.title}</span>
        </Link>)}
      </nav>
      <div className="ndr-settings-command" data-testid="settings-actions">
        <button className="ndr-primary" disabled={busy || !anyChanged}
          title={busy ? '正在保存或重启，请等待完成。' : !anyChanged ? '当前设置已保存。' : '保存全部已修改的设置。'}
          onClick={() => void saveAll()}>保存设置</button>
        {selected === 'application-settings' && <button disabled={Boolean(restartReason)} title={restartReason ?? '保存全部设置并重启服务。'}
          onClick={() => { if (window.confirm('保存全部设置并重启服务？应用配置将在重启后生效。')) void saveAll(true) }}>保存并重启</button>}
      </div>
      <div className="ndr-settings-feedback">
        {categories.map(category => <p key={category.id} id={`${category.id}-description`} className="hint"
          hidden={describedCategory !== category.id}>{category.description}</p>)}
        {saveError ? <p className="status-error" role="alert">保存失败：{saveError}</p>
          : <p className="hint" role="status" hidden={Boolean(describedCategory) && !saving && !applicationState.restarting}>{applicationState.restarting ? '设置已保存，服务正在重启。' : saving ? '正在保存全部修改…' : anyChanged ? '有未保存修改，点击保存全部设置。' : saved ? savedApplication ? '设置已保存，应用配置重启后生效。' : '阅读与处理设置已保存。' : '保存全部已修改的偏好与应用配置。'}</p>}
        {selected === 'application-settings' && <p className="hint">应用配置保存后重启生效。{restartReason}</p>}
        {applicationState.restarting && <a className="ndr-button" href={`${applicationState.restartAddress}/settings/general#application-settings`}>重新打开设置</a>}
      </div>
    </div>
    <fieldset className="ndr-settings-content" disabled={busy}>
    <section className="card" id="reading-settings" hidden={selected !== 'reading-settings'}>
      <h3>阅读显示</h3>

      <div className="ndr-range-grid">
        <label className="ndr-field">正文字号<input title="范围14至28，保存后应用于正文与处理页预览的字号。" type="number" min={14} max={28} value={settings.fontSize} onChange={e => update({ fontSize: Number(e.target.value) })} /></label>
        <label className="ndr-field">正文行距<input title="范围1.5至2.6，保存后应用于正文与处理页预览的行距。" type="number" min={1.5} max={2.6} step={0.05} value={settings.lineHeight} onChange={e => update({ lineHeight: Number(e.target.value) })} /></label>
      </div>
      <div className="ndr-settings-options">
        <label><input type="checkbox" title="重新打开书籍时回到上次阅读位置。关闭后从开头阅读，最后位置仍持续保存。" checked={settings.resumeReading} onChange={e => update({ resumeReading: e.target.checked })} />重新打开时自动续读</label>
        <label><input type="checkbox" title="下次进入阅读页时显示检测到的引语范围；阅读中可临时调整。" checked={settings.showCandidates} onChange={e => update({ showCandidates: e.target.checked })} />阅读页默认显示候选引语</label>
        <label><input type="checkbox" title="下次进入阅读页时显示已确认的人物颜色与编号；阅读中可临时调整。" checked={settings.showAnnotations} onChange={e => update({ showAnnotations: e.target.checked })} />阅读页默认显示人物标注</label>
        <label><input type="checkbox" title="在阅读与处理页预览中标记待确认的对白，保存后生效。" checked={settings.showReviewMarkers} onChange={e => update({ showReviewMarkers: e.target.checked })} />显示待确认对白标记</label>
        <label><input type="checkbox" title="双击目录章节名可在未处理与已完成之间切换。处理中、失败或已停止的章节先标为已完成；标注保留，已发送的模型请求继续执行。" checked={settings.doubleClickChapterStatus} onChange={e => update({ doubleClickChapterStatus: e.target.checked })} />双击目录章节名切换完成状态</label>
      </div>
      <p className="hint">字号、行距与待确认标记保存后生效；候选引语和人物标注在下次进入阅读页时使用。</p>


      <div className="ndr-document ndr-reading-sample" style={{ fontSize: `${settings.fontSize}px`, lineHeight: settings.lineHeight }}><p>阅读样例：「雨停了。」少女合上伞。</p></div>
      <button onClick={() => {
        if (!window.confirm('恢复阅读显示的默认值？点击“保存设置”后生效。')) return
        const { fontSize, lineHeight, resumeReading, showCandidates, showAnnotations, showReviewMarkers, doubleClickChapterStatus } = defaultSettings
        update({ fontSize, lineHeight, resumeReading, showCandidates, showAnnotations, showReviewMarkers, doubleClickChapterStatus })
      }}>恢复阅读显示默认值</button>
    </section>
    <section className="card" id="character-settings" hidden={selected !== 'character-settings'}>
      <h3>人物资料更新</h3>
      <label><input type="checkbox" title="批量与自动处理可根据原文更新人工姓名和说明；新建人物任务使用此设置，单章确认时由你选择。" checked={settings.allowOverwriteManualCharacters} onChange={event => update({ allowOverwriteManualCharacters: event.target.checked })} />允许后台人物识别更新人工姓名与说明</label>
      <p className="hint">允许批量与自动处理根据原文更新人工姓名和说明。模型可能误判，新建人物任务使用此设置；单章确认时由你选择。</p>
      <button onClick={() => {
        if (window.confirm('恢复人物资料更新的默认值，关闭后台覆盖人工资料？点击保存后生效。')) {
          update({ allowOverwriteManualCharacters: defaultSettings.allowOverwriteManualCharacters })
        }
      }}>恢复人物资料更新默认值</button>
    </section>
    <div id="chapter-filter-settings" hidden={selected !== 'chapter-filter-settings'}><ChapterFilterSettings value={settings} onChange={update} /></div>
    <section className="card" id="automatic-settings" hidden={selected !== 'automatic-settings'} data-testid="automatic-processing-settings">
      <h3>自动处理</h3>
      <div className="ndr-settings-group ndr-automatic-schedule">
        <div>
          <label><input type="checkbox" title="阅读时自动处理当前章及后续章节，已完成章节复用结果。请保持应用页面打开。" checked={settings.autoProcessing}
            onChange={event => update({ autoProcessing: event.target.checked })} />阅读时自动处理章节（会调用模型）</label>
          <p className="hint">处理当前章及后续章节，可能产生费用。请保持应用页面打开。</p>
        </div>
        <label className="ndr-field" hidden={!settings.autoProcessing}>提前处理后续章节数<input title="自动处理当前章及此数量的后续章节，0表示只处理当前章。" type="number" min={0} max={100} value={settings.lookAheadChapters} onChange={event => update({ lookAheadChapters: Number(event.target.value) })} /></label>
      </div>
      <ThinkingSettings disabled={false} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => updatePreferences({ profileId })} profileTestId="automatic-profile"
        draftPreferences={preferences} onPreferencesChange={updatePreferences} />
      {profiles.isError && <p className="status-error">模型配置读取失败：{profiles.error.message}<button onClick={() => void profiles.refetch()}>重新读取</button></p>}
      <section className="ndr-settings-group" aria-label="并发与额度">
        <h4>并发与额度</h4>
        <div className="ndr-range-grid ndr-settings-fields">
          <label className="ndr-field">最大并发任务数<input title="同时处理的最大任务数，单章、批量与自动处理共用。" type="number" min={1} max={16} value={preferences.concurrency} onChange={event => updatePreferences({ concurrency: Number(event.target.value) })} /></label>
          <label className="ndr-field">自动处理 Token 上限（留空＝不限）<input title="自动处理按本页会话中每本书累计额度；阅读侧栏可停止、刷新额度并重试。单章与批量也使用此额度偏好。" type="number" min={1} value={preferences.tokenLimit ?? ''} onChange={event => updatePreferences({ tokenLimit: event.target.value ? Number(event.target.value) : null })} /></label>
          <label className="ndr-field">每个窗口最多复核次数（0 关闭）<input title="每轮检查窗口内全部对白；0表示关闭复核。额外复核会增加模型用量。" type="number" min={0} value={preferences.maxRecheckRounds} onChange={event => updatePreferences({ maxRecheckRounds: Number(event.target.value) })} /></label>
          <label className="ndr-field">校验失败后的重试次数（与对白共用）<input title="人物分析与对白共用，最多5次。格式重试与人物证据修复分别计数，额外调用会增加用量。" type="number" min={0} max={5} value={preferences.maxFormatRetries} onChange={event => updatePreferences({ maxFormatRetries: Number(event.target.value) })} /></label>
        </div>
      </section>
      <p className="hint">模型、思考与处理参数由单章、批量和自动处理共用，保存后用于新任务。</p>
      <div className="ndr-settings-actions">
        <Link className="ndr-button" to="/settings/models">管理模型账号</Link>
        <button title="恢复本栏默认值后请保存；模型账号在模型配置页管理，输出上限在处理页设置。" onClick={() => {
          if (!window.confirm('恢复自动处理、提前章节数、模型、思考、并发、复核、重试和 Token 上限默认值？点击保存后生效。')) return
          update({ autoProcessing: defaultSettings.autoProcessing, lookAheadChapters: defaultSettings.lookAheadChapters })
          const { profileId, concurrency, tokenLimit, maxRecheckRounds, thinkingMode, thinkingEffort, maxFormatRetries } = getDefaultProcessingPreferences()
          updatePreferences({ profileId, concurrency, tokenLimit, maxRecheckRounds, thinkingMode, thinkingEffort, maxFormatRetries })
        }}>恢复自动处理默认值</button>
      </div>
    </section>
    <section className="card" id="experimental-settings" hidden={selected !== 'experimental-settings'}>
      <h3>试验功能</h3>
      <label><input type="checkbox" title="解锁下方功能选择；保存后在当前浏览器生效。关闭时保留各项选择。" checked={settings.enableExperimentalFeatures}
        onChange={event => update({ enableExperimentalFeatures: event.target.checked })} />启用试验功能</label>
      <p className="hint">{settings.enableExperimentalFeatures ? '选择需要的功能，保存后生效。试验效果仍需核对。' : '先启用试验功能，再选择下方功能。'}</p>
      <fieldset className="ndr-experimental-list" disabled={!settings.enableExperimentalFeatures} aria-label="试验功能列表">
        <section className="ndr-experiment-item">
          <label><input type="checkbox" title="开放初读模式、初读安全导出，以及人物修改和合并的可见章节选择。" checked={settings.enableInitialReading}
            onChange={event => update({ enableInitialReading: event.target.checked })} />初读与安全导出</label>
          <p className="hint">按阅读进度呈现人物身份与标注，适合首次阅读。</p>
          <p className="hint">关闭时阅读与导出使用重读，可能显示后文才揭示的人名。</p>
        </section>
        <section className="ndr-experiment-item">
          <label><input type="checkbox" title="开放完整上下文对白策略。各方案按需选一，保存后用于新任务。" checked={settings.enableDialogueStrategies}
            onChange={event => update({ enableDialogueStrategies: event.target.checked })} />试验对白策略</label>
          <p className="hint">结合完整原文判断对白归属，可选择辅助隔离、独立复核或人物反馈方案。</p>
          <div hidden={!settings.enableDialogueStrategies}>
            <DialogueStrategySettings compact value={preferences.dialogueStrategy} rounds={preferences.maxRecheckRounds}
              experimentalEnabled={true} onChange={dialogueStrategy => updatePreferences({ dialogueStrategy })} />
            <p className="hint">复核次数在“自动处理”中调整。</p>
          </div>
        </section>
        <div className="ndr-experiment-item">
          <RosterRepairSettings compact preferences={preferences} onChange={updatePreferences} experimentalEnabled={true} />
        </div>
      </fieldset>
    </section>

    <div hidden={selected !== 'application-settings'}><ApplicationSettings ref={application} managed onStateChange={applicationStateChanged} /></div>
    </fieldset>
    </div>
  </div>
}
