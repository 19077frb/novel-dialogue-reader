/**
 * 后端 API 访问封装。
 *
 * 公共约定见 docs/CONTRACTS.md：JSON snake_case、UTC ISO 8601、
 * 错误体为 { error: { code, message, details }, request_id }。
 */

const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export interface ApiErrorBody {
  code: string
  message: string
  details?: unknown
  request_id?: string
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: unknown

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, headers } = options
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    signal,
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })

  if (!response.ok) {
    let payload: ApiErrorBody = {
      code: `HTTP_${response.status}`,
      message: `请求失败（HTTP ${response.status}）`,
    }
    try {
      const parsed = (await response.json()) as { error?: ApiErrorBody }
      if (parsed?.error?.code) {
        payload = { ...parsed.error, request_id: parsed.error.request_id }
      }
    } catch {
      // 保持上面的兜底错误信息，不吞掉状态码。
    }
    throw new ApiError(response.status, payload)
  }

  return (await response.json()) as T
}

export interface DatabaseStatus {
  state: 'READY' | 'NOT_INITIALIZED' | 'ERROR'
  detail?: string | null
}

export interface HealthResponse {
  status: 'ok'
  app: string
  version: string
  api_version: string
  environment: string
  started_at: string
  uptime_seconds: number
  server_time: string
  database: DatabaseStatus
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/api/health', { signal })
}
