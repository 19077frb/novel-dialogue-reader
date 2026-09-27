import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as client from '../src/api/client'
import App from '../src/App'

vi.mock('../src/api/client', () => ({
  fetchHealth: vi.fn(),
}))

const mockedFetchHealth = vi.mocked(client.fetchHealth)

function renderApp() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  )
}

describe('App', () => {
  beforeEach(() => {
    mockedFetchHealth.mockReset()
  })

  it('展示后端返回的状态与版本，而不是硬编码文本', async () => {
    mockedFetchHealth.mockResolvedValue({
      status: 'ok',
      app: 'novel-dialogue-reader',
      version: '9.9.9-test',
      api_version: '1',
      environment: 'test',
      started_at: '2026-09-28T00:00:00+00:00',
      uptime_seconds: 12.5,
      server_time: '2026-09-28T00:00:12.500000+00:00',
      database: { state: 'NOT_INITIALIZED', detail: '数据库与迁移在 T01 建立' },
    })

    renderApp()

    expect(await screen.findByTestId('health-ok')).toBeInTheDocument()
    expect(screen.getByText('9.9.9-test')).toBeInTheDocument()
    expect(screen.getByText(/NOT_INITIALIZED/)).toBeInTheDocument()
  })

  it('后端不可用时给出可理解的错误与重试入口', async () => {
    mockedFetchHealth.mockRejectedValue(new Error('连接被拒绝'))

    renderApp()

    expect(await screen.findByTestId('health-error')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试连接' })).toBeInTheDocument()
    expect(screen.getByText(/连接被拒绝/)).toBeInTheDocument()
  })
})
