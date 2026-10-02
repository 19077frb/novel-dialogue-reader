import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchProfiles, profileKeys } from '../api/profiles'
import { ThinkingSettings } from '../components/ThinkingSettings'
import { useProcessingPreferences } from '../processing/preferences'
import { stopAutomaticProcessing } from '../processing/autoProcessing'
import { defaultSettings, useGeneralSettings } from '../settings/preferences'

export default function SettingsPage() {
  const [settings, update] = useGeneralSettings()
  const [preferences, updatePreferences] = useProcessingPreferences()
  const profiles = useQuery({ queryKey: profileKeys.profiles(), queryFn: ({ signal }) => fetchProfiles(signal) })
  return <div className="ndr-page">
    <header className="card ndr-page-header"><h2>通用设置</h2><p className="hint">阅读显示偏好自动保存到当前浏览器，重启与切换页面后保留。</p></header>
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
        <label><input type="checkbox" checked={settings.doubleClickChapterStatus} onChange={e => update({ doubleClickChapterStatus: e.target.checked })} />双击目录章节名切换完成状态</label>
      </div>
      <p className="hint">显示开关在下一次进入阅读页时作为默认值；阅读中仍可临时调整。字号和行距也用于处理页原文预览，不改变导出样式。</p>
      <p className="hint">双击切换默认关闭，开启后立即生效：未处理与已完成互相切换；处理中、失败或已停止先标为已完成，再次双击改为未处理。仅改完成标记，不删除标注或停止已发送的模型请求；重新启动本章处理后恢复自动进度更新。</p>
      <div className="ndr-document"><p>阅读样例：「雨停了。」少女合上伞。</p></div>
      <button onClick={() => { if (window.confirm('恢复通用设置默认值？不会清除书籍、书签或模型配置。')) update(defaultSettings) }}>恢复默认设置</button>
    </section>
    <section className="card">
      <h3>人物资料更新</h3>
      <label><input type="checkbox" checked={settings.allowOverwriteManualCharacters} onChange={event => update({ allowOverwriteManualCharacters: event.target.checked })} />允许后台人物识别更新人工姓名与说明</label>
      <p className="hint">默认关闭。开启后，批量处理及阅读时的自动处理可以根据有原文依据的人物识别结果修正人工资料；模型可能误判。设置自动保存，仅影响之后创建的人物任务，已有任务保持原设置。单章人工确认仍由你选择。</p>
    </section>
    <section className="card" data-testid="automatic-processing-settings">
      <h3>自动提前处理章节</h3>
      <label><input type="checkbox" checked={settings.autoProcessing} onChange={event => {
        if (!event.target.checked) stopAutomaticProcessing()
        update({ autoProcessing: event.target.checked })
      }} />阅读时自动处理当前章及后续章节（会调用模型）</label>
      <p className="hint">默认关闭。开启后会自动识别人物并确认对白归属，可能消耗付费额度；已完成章节会跳过。请保持页面打开，可切换到应用内其他页面。</p>
      <div className="ndr-range-grid">
        <label className="ndr-field">提前处理后续章节数<input type="number" min={0} max={100} value={settings.lookAheadChapters} onChange={event => update({ lookAheadChapters: Number(event.target.value) })} /></label>
        <label className="ndr-field">最大并发任务数<input type="number" min={1} max={16} value={preferences.concurrency} onChange={event => updatePreferences({ concurrency: Number(event.target.value) })} /></label>
        <label className="ndr-field">自动处理 Token 上限（留空＝不限）<input type="number" min={1} value={preferences.tokenLimit ?? ''} onChange={event => updatePreferences({ tokenLimit: event.target.value ? Number(event.target.value) : null })} /></label>
        <label className="ndr-field">每个窗口最多复核数<input type="number" min={0} value={preferences.maxRechecks} onChange={event => updatePreferences({ maxRechecks: Number(event.target.value) })} /></label>
      </div>
      <ThinkingSettings disabled={false} profiles={profiles.data ?? []} profileId={preferences.profileId}
        onProfileChange={profileId => updatePreferences({ profileId })} profileTestId="automatic-profile" />
      {profiles.isError && <p className="status-error">模型配置读取失败：{profiles.error.message}<button onClick={() => void profiles.refetch()}>重新读取</button></p>}
      <p className="hint">这些模型、思考、并发和额度设置与单章、批量处理共用。自动处理额度按本页会话中每本书累计；接近上限时会提醒调整，阅读侧栏可停止或刷新额度并重试。修改设置不改变已启动的任务。</p>
      <Link className="ndr-button" to="/settings/models">管理模型账号</Link>
    </section>
  </div>
}
