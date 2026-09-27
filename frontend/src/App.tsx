import { useQuery } from '@tanstack/react-query'

import { fetchHealth } from './api/client'

const PLANNED_PAGES: ReadonlyArray<{ path: string; label: string; task: string }> = [
  { path: '/library', label: '书架与导入', task: 'T04' },
  { path: '/settings/models', label: '模型配置', task: 'T06' },
  { path: '/books/:id/preview', label: '效果预览', task: 'T11' },
  { path: '/books/:id/read', label: '阅读', task: 'T04/T11' },
  { path: '/books/:id/review', label: '待确认', task: 'T13' },
]

export default function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => fetchHealth(signal),
    retry: false,
  })

  return (
    <main className="app-shell">
      <header>
        <h1>轻小说对话辅助阅读器</h1>
        <p className="subtitle">
          本地运行的对白着色与待确认辅助工具。当前处于 <strong>T00 工程骨架</strong> 阶段，
          业务页面尚未实现。
        </p>
      </header>

      <section className="card" aria-labelledby="health-heading">
        <h2 id="health-heading">后端连接状态</h2>
        <p className="hint">
          此状态来自后端 <code>GET /api/health</code>，不是前端硬编码值。
        </p>

        {health.isPending && <p data-testid="health-loading">正在连接后端…</p>}

        {health.isError && (
          <div data-testid="health-error">
            <p className="status status-error">无法连接后端</p>
            <p className="hint">
              请确认后端已在 127.0.0.1:8765 运行（<code>scripts/dev.ps1</code>）。
            </p>
            <p className="hint">错误：{health.error instanceof Error ? health.error.message : '未知错误'}</p>
            <button type="button" onClick={() => void health.refetch()}>
              重试连接
            </button>
          </div>
        )}

        {health.isSuccess && (
          <dl data-testid="health-ok">
            <dt>状态</dt>
            <dd className="status status-ok">{health.data.status}</dd>
            <dt>应用版本</dt>
            <dd>{health.data.version}</dd>
            <dt>契约版本</dt>
            <dd>{health.data.api_version}</dd>
            <dt>运行环境</dt>
            <dd>{health.data.environment}</dd>
            <dt>数据库</dt>
            <dd>
              {health.data.database.state}
              {health.data.database.detail ? ` — ${health.data.database.detail}` : ''}
            </dd>
            <dt>已运行</dt>
            <dd>{health.data.uptime_seconds} 秒</dd>
          </dl>
        )}
      </section>

      <section className="card" aria-labelledby="planned-heading">
        <h2 id="planned-heading">后续任务将启用的页面（尚未实现）</h2>
        <ul>
          {PLANNED_PAGES.map((page) => (
            <li key={page.path}>
              <code>{page.path}</code> — {page.label}（{page.task}）
            </li>
          ))}
        </ul>
      </section>
    </main>
  )
}
