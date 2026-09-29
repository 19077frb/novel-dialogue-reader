/**
 * 后端 API 客户端。
 *
 * 公共约定见 docs/CONTRACTS.md：JSON snake_case、UTC ISO 8601、
 * 错误体为 { error: { code, message, details }, request_id }。
 */
const API_BASE: string = import.meta.env.VITE_API_BASE ?? ''

export interface ApiErrorBody {
  code: string
  message: string
  details?: Record<string, unknown>
  request_id?: string
}

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly details: Record<string, unknown>

  constructor(status: number, body: ApiErrorBody) {
    super(body.message)
    this.name = 'ApiError'
    this.status = status
    this.code = body.code
    this.details = body.details ?? {}
  }
}

/** GET 查询只对网络错误、限流和服务端临时错误做至多两次重试。 */
export function shouldRetryReadRequest(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false
  if (!(error instanceof ApiError)) return true
  return error.status === 408 || error.status === 429 || error.status >= 500
}

export function readRetryDelay(attemptIndex: number): number {
  return Math.min(1_000, 200 * (2 ** attemptIndex))
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
  headers?: Record<string, string>
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let payload: ApiErrorBody = {
      code: `HTTP_${response.status}`,
      message: `请求失败（HTTP ${response.status}）`,
    }
    try {
      const parsed = (await response.json()) as { error?: ApiErrorBody }
      if (parsed?.error?.code) {
        payload = parsed.error
      }
    } catch {
      // 保持兜底信息，不吞掉状态码。
    }
    throw new ApiError(response.status, payload)
  }
  return (await response.json()) as T
}

export function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, headers } = options
  return fetch(`${API_BASE}${path}`, {
    method,
    signal,
    headers: {
      Accept: 'application/json',
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((response) => readJson<T>(response))
}

/** 取 `{"data": ...}` 包里的数据。 */
export async function apiData<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const envelope = await apiRequest<{ data: T }>(path, options)
  return envelope.data
}

/** multipart 上传（导入文件）。 */
export async function apiUpload<T>(
  path: string,
  form: FormData,
  options: { signal?: AbortSignal } = {},
): Promise<T> {
  const envelope = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    body: form,
    signal: options.signal,
    headers: { Accept: 'application/json' },
  }).then((response) => readJson<{ data: T }>(response))
  return envelope.data
}

export interface DatabaseStatus {
  state: 'READY' | 'NOT_INITIALIZED' | 'OUTDATED' | 'ERROR'
  revision?: string
  head_revision?: string
  detail?: string | null
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
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
