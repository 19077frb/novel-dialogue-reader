import { Link } from 'react-router-dom'
import { defaultSettings, useGeneralSettings } from '../settings/preferences'

export default function SettingsPage() {
  const [settings, update] = useGeneralSettings()
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
      </div>
      <p className="hint">显示开关在下一次进入阅读页时作为默认值；阅读中仍可临时调整。字号和行距也用于处理页原文预览，不改变导出样式。</p>
      <div className="ndr-document"><p>阅读样例：「雨停了。」少女合上伞。</p></div>
      <button onClick={() => { if (window.confirm('恢复通用设置默认值？不会清除书籍、书签或模型配置。')) update(defaultSettings) }}>恢复默认设置</button>
    </section>
    <section className="card"><h3>模型与处理配置</h3><p className="hint">模型账号、地址与密钥在模型配置中管理；处理额度和并发等仍在预览与处理中调整并自动记忆。</p><Link className="ndr-button" to="/settings/models">模型配置</Link></section>
  </div>
}
