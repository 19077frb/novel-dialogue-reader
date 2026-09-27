import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

function withProviders(children: ReactElement, route: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
    </QueryClientProvider>,
  )
}

/** 直接渲染一个组件（例如页面本身）。 */
export function renderWithProviders(ui: ReactElement, route = '/') {
  return withProviders(ui, route)
}

/** 按路由渲染（需要 useParams 的页面必须用这个）。 */
export function renderRoute(path: string, element: ReactElement, route: string) {
  return withProviders(
    <Routes>
      <Route path={path} element={element} />
    </Routes>,
    route,
  )
}