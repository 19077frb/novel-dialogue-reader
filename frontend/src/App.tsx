import { useQuery } from '@tanstack/react-query'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { queryKeys } from './api/books'
import { fetchHealth } from './api/client'
import LibraryPage from './pages/LibraryPage'
import ModelSettingsPage from './pages/ModelSettingsPage'
import PreviewPage from './pages/PreviewPage'
import ReviewPage from './pages/ReviewPage'
import ReaderPage from './pages/ReaderPage'

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
        <p className="hint">请确认后端已在 127.0.0.1:8765 运行（scripts/dev.ps1）。</p>
        <button type="button" onClick={() => void health.refetch()}>
          重试连接
        </button>
      </div>
    )
  }

  return (
    <dl className="ndr-health" data-testid="health-ok">
      <dt>状态</dt>
      <dd className="status status-ok">{health.data.status}</dd>
      <dt>版本</dt>
      <dd>{health.data.version}</dd>
      <dt>数据库</dt>
      <dd>
        {health.data.database.state}
        {health.data.database.detail ? ` — ${health.data.database.detail}` : ''}
      </dd>
    </dl>
  )
}

export default function App() {
  return (
    <div className="app-shell">
      <a className="ndr-skip-link" href="#ndr-main">
        跳到主要内容
      </a>
      <header className="ndr-app-header">
        <div className="ndr-brand">
          <h1>轻小说对话辅助阅读器</h1>
          <p className="subtitle">
            导入 TXT/EPUB 后即可阅读原文；着色、场景与待确认队列随 T05 起接入。
          </p>
        </div>
        <nav className="ndr-app-nav" aria-label="主导航">
          {/* NavLink 会在当前页给出 aria-current="page"，键盘/读屏用户能知道自己在哪一页 */}
          <NavLink to="/library">书架</NavLink>
          <NavLink to="/settings/models">模型配置</NavLink>
        </nav>
        <HealthBadge />
      </header>

      <main className="ndr-main" id="ndr-main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/books/:bookId/read" element={<ReaderPage />} />
          <Route path="/books/:bookId/preview" element={<PreviewPage />} />
          <Route path="/books/:bookId/review" element={<ReviewPage />} />
          <Route path="/settings/models" element={<ModelSettingsPage />} />
          <Route path="*" element={<p className="status-error">页面不存在。</p>} />
        </Routes>
      </main>
    </div>
  )
}
