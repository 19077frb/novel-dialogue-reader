import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { queryKeys } from './api/books'
import { fetchHealth } from './api/client'
import LibraryPage from './pages/LibraryPage'
import ModelSettingsPage from './pages/ModelSettingsPage'
import PreviewPage from './pages/PreviewPage'
import ReviewPage from './pages/ReviewPage'
import ReaderPage from './pages/ReaderPage'
import CharactersPage from './pages/CharactersPage'
import BookmarksPage from './pages/BookmarksPage'
import PreprocessingPage from './pages/PreprocessingPage'
import SettingsPage from './pages/SettingsPage'
import TaskQueuePage from './pages/TaskQueuePage'
import { useGeneralSettings } from './settings/preferences'
import { stopAutomaticProcessing } from './processing/autoProcessing'
import { SavedTaskRecovery } from './components/SavedTaskRecovery'

/** 后端连接状态：显示真实 /api/health 结果（不调用模型）。 */
export function HealthBadge() {
  const health = useQuery({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => fetchHealth(signal),
    retry: false,
  })

  if (health.isPending) return <p data-testid="health-loading" className="hint">正在连接后端…</p>

  if (health.isError) {
    return (
      <div data-testid="health-error" className="ndr-health-error">
        <p className="status status-error">无法连接后端</p>
        <p className="hint">请确认应用启动窗口仍然打开，再重试连接。如果修改过服务地址，请使用启动时显示的地址。</p>
        <button type="button" onClick={() => void health.refetch()}>
          重试连接
        </button>
      </div>
    )
  }

  return (
    <div className="ndr-health-pill" data-testid="health-ok">
      <span className="ndr-health-dot" aria-hidden="true" />
      <span title={`服务：${health.data.status}；数据库：${health.data.database.state}`}>
        {health.data.status === 'ok' ? '服务已连接' : health.data.status === 'degraded' ? '服务异常' : health.data.status} · v{health.data.version} · 数据库 {({ READY: '就绪', ERROR: '异常', OUTDATED: '需要升级', NOT_INITIALIZED: '尚未初始化' } as Record<string, string>)[health.data.database.state] ?? health.data.database.state}
        {health.data.database.detail ? ` — ${health.data.database.detail}` : ''}
      </span>
    </div>
  )
}

export default function App() {
  const [settings] = useGeneralSettings()
  useEffect(() => { if (!settings.autoProcessing) stopAutomaticProcessing() }, [settings.autoProcessing])
  useEffect(() => {
    document.documentElement.style.setProperty('--ndr-reading-font-size', `${settings.fontSize}px`)
    document.documentElement.style.setProperty('--ndr-reading-line-height', String(settings.lineHeight))
  }, [settings.fontSize, settings.lineHeight])
  return (
    <div className="app-shell">
      <a className="ndr-skip-link" href="#ndr-main">
        跳到主要内容
      </a>
      <header className="ndr-app-header">
        <div className="ndr-brand">
          <h1>轻小说对话辅助阅读器</h1>
          <p className="subtitle">
            导入 TXT/EPUB 后即可阅读原文；识别说话人后按人物着色并编号，不确定的内容进入待确认队列。
          </p>
        </div>
        <nav className="ndr-app-nav" aria-label="主导航">
          {/* NavLink 会在当前页给出 aria-current="page"，键盘/读屏用户能知道自己在哪一页 */}
          <NavLink to="/library">书架</NavLink>
          <NavLink to="/tasks">任务队列</NavLink>
          <NavLink to="/settings/models">模型配置</NavLink>
          <NavLink to="/settings/general">通用设置</NavLink>
        </nav>
        <HealthBadge />
        <SavedTaskRecovery />
      </header>

      <main className="ndr-main" id="ndr-main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/tasks" element={<TaskQueuePage />} />
          <Route path="/books/:bookId/read" element={<ReaderPage />} />
          <Route path="/books/:bookId/bookmarks" element={<BookmarksPage />} />
          <Route path="/books/:bookId/preprocessing" element={<PreprocessingPage />} />
          <Route path="/books/:bookId/characters" element={<CharactersPage />} />
          <Route path="/books/:bookId/preview" element={<PreviewPage />} />
          <Route path="/books/:bookId/review" element={<ReviewPage />} />
          <Route path="/settings/models" element={<ModelSettingsPage />} />
          <Route path="/settings/general" element={<SettingsPage />} />
          <Route path="*" element={<p className="status-error">页面不存在。</p>} />
        </Routes>
      </main>
    </div>
  )
}
