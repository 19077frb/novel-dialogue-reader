import { fetchHealth } from './client'
import type { HealthResponse } from './client'
import type { ApplicationSettings } from './applicationSettings'

const REQUEST_TIMEOUT = 2_000
const RESTART_TIMEOUT = 180_000
export type ApplicationRestartBaseline = { startedAt: string; serverTime: string }

function abortError() { return new DOMException('已取消检测', 'AbortError') }

export async function readRestartHealth(origin?: string, signal?: AbortSignal): Promise<HealthResponse> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  if (signal?.aborted) controller.abort()
  const timeout = window.setTimeout(abort, REQUEST_TIMEOUT)
  try {
    const health = origin
      ? await fetch(`${origin}/api/health`, { signal: controller.signal, cache: 'no-store', credentials: 'omit' })
        .then(async response => {
          if (!response.ok) throw new Error(`健康检查返回 HTTP ${response.status}`)
          return await response.json() as HealthResponse
        })
      : await fetchHealth(controller.signal)
    if (controller.signal.aborted) throw abortError()
    if (health.app !== 'novel-dialogue-reader' || !Number.isFinite(Date.parse(health.started_at))
      || !Number.isFinite(Date.parse(health.server_time))) throw new Error('目标地址未返回阅读器服务状态')
    return health
  } finally {
    window.clearTimeout(timeout)
    signal?.removeEventListener('abort', abort)
  }
}

function delay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { window.clearTimeout(timer); reject(abortError()) }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 1_000)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
  })
}

export async function waitForApplicationRestart(baseline: ApplicationRestartBaseline, origin: string | undefined, signal: AbortSignal): Promise<void> {
  const controller = new AbortController()
  const abort = () => controller.abort()
  signal.addEventListener('abort', abort, { once: true })
  if (signal.aborted) controller.abort()
  const timeout = window.setTimeout(abort, RESTART_TIMEOUT)
  let detail = '尚未收到新服务的就绪状态'
  try {
    while (!controller.signal.aborted) {
      try {
        const health = await readRestartHealth(origin, controller.signal)
        const newInstance = health.started_at !== baseline.startedAt && Date.parse(health.started_at) > Date.parse(baseline.serverTime)
        if (newInstance && health.status === 'ok' && health.database?.state === 'READY') return
        detail = !newInstance ? '尚未检测到本次重启后的新服务' : `新服务数据库状态：${health.database?.state ?? '未知'}`
      } catch (error) {
        detail = error instanceof Error && error.name !== 'AbortError' ? error.message : '等待服务连接恢复'
      }
      await delay(controller.signal)
    }
  } catch (error) {
    if (!controller.signal.aborted) throw error
  } finally {
    window.clearTimeout(timeout)
    signal.removeEventListener('abort', abort)
  }
  if (signal.aborted) throw abortError()
  throw new Error(`设置已保存，180 秒内尚未确认服务恢复。${detail}。请重新检测或打开目标地址，查看启动窗口的具体错误。`)
}

export function applicationRestartTarget(settings: ApplicationSettings): { origin: string | undefined; address: string } {
  const host = settings.fields.find(field => field.key === 'host')
  const port = settings.fields.find(field => field.key === 'port')
  const changed = [host, port].some(field => field && field.value !== field.current_value)
  if (!changed) return { origin: undefined, address: window.location.origin }
  const hostname = String(host?.value ?? '127.0.0.1')
  const origin = `http://${hostname === '::1' ? '[::1]' : hostname}:${String(port?.value ?? 8765)}`
  return { origin, address: origin }
}

export function openRestartedSettings(address: string) {
  const target = new URL(`${address}/settings/general#application-settings`)
  const current = new URL(window.location.href)
  if (target.origin === current.origin && target.pathname === current.pathname && target.search === current.search) {
    window.history.replaceState(window.history.state, '', target.href)
    window.location.reload()
  } else window.location.replace(target.href)
}
