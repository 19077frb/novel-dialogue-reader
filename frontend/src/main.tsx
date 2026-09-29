import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import App from './App'
import { readRetryDelay, shouldRetryReadRequest } from './api/client'
import './styles/global.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 阅读期间数据库可能正被后台批处理写入；只读请求短暂失败时有限重试。
      retry: shouldRetryReadRequest,
      retryDelay: readRetryDelay,
      staleTime: 5_000,
    },
  },
})

const container = document.getElementById('root')
if (!container) {
  throw new Error('缺少 #root 挂载点')
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
