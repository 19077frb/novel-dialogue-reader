import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { applicationRestartTarget, openRestartedSettings, readRestartHealth, waitForApplicationRestart } from '../src/api/applicationRestart'
import * as client from '../src/api/client'
import type { HealthResponse } from '../src/api/client'
import type { ApplicationSettings } from '../src/api/applicationSettings'

vi.mock('../src/api/client', () => ({ fetchHealth: vi.fn() }))
const health: HealthResponse = {
  app: 'novel-dialogue-reader', status: 'ok', started_at: '2026-11-17T10:00:00Z', database: { state: 'READY' },
  version: '1', api_version: '1', environment: 'test', uptime_seconds: 1, server_time: '2026-11-17T10:05:00Z',
}
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks() })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.restoreAllMocks(); localStorage.clear() })

it('保留原 API 通道，原实例、断连及尚未就绪均继续等待新实例', async () => {
  vi.mocked(client.fetchHealth).mockResolvedValueOnce(health)
    .mockRejectedValueOnce(new Error('连接中断'))
    .mockResolvedValueOnce({ ...health, started_at: '2026-11-17T10:05:01Z', database: { state: 'OUTDATED' } })
    .mockResolvedValueOnce({ ...health, started_at: '2026-11-17T10:05:01Z' })
  const controller = new AbortController()
  const complete = vi.fn()
  const waiting = waitForApplicationRestart({ startedAt: health.started_at, serverTime: health.server_time }, undefined, controller.signal).then(complete)
  await vi.advanceTimersByTimeAsync(2_000)
  expect(complete).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1_000)
  await waiting
  expect(complete).toHaveBeenCalledTimes(1)
  expect(client.fetchHealth).toHaveBeenCalledTimes(4)
  expect(vi.getTimerCount()).toBe(0)
})

it('新地址只接受正确应用的新实例 READY，GET 不携带凭据或缓存', async () => {
  const fetch = vi.fn().mockResolvedValueOnce({ ok: true, json: async () => ({ ...health, app: 'other', started_at: '2026-11-17T10:05:01Z' }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...health, started_at: '2026-11-17T10:01:00Z' }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ ...health, started_at: '2026-11-17T10:05:01Z' }) })
  vi.stubGlobal('fetch', fetch)
  const waiting = waitForApplicationRestart({ startedAt: health.started_at, serverTime: health.server_time }, 'http://127.0.0.1:8800', new AbortController().signal)
  await vi.advanceTimersByTimeAsync(2_000)
  await waiting
  expect(fetch).toHaveBeenCalledTimes(3)
  expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:8800/api/health', expect.objectContaining({
    cache: 'no-store', credentials: 'omit', signal: expect.any(AbortSignal),
  }))
  expect(client.fetchHealth).not.toHaveBeenCalled()
})

it('健康请求两秒超时，整体180秒后报告结果并清理定时器', async () => {
  const signals: AbortSignal[] = []
  vi.mocked(client.fetchHealth).mockImplementation(signal => new Promise((_resolve, reject) => {
    signals.push(signal!)
    signal!.addEventListener('abort', () => reject(new DOMException('timeout', 'AbortError')), { once: true })
  }))
  const waiting = waitForApplicationRestart({ startedAt: health.started_at, serverTime: health.server_time }, undefined, new AbortController().signal)
  const result = expect(waiting).rejects.toThrow('180 秒内尚未确认服务恢复')
  await vi.advanceTimersByTimeAsync(1_999)
  expect(signals[0].aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(1)
  expect(signals[0].aborted).toBe(true)
  await vi.advanceTimersByTimeAsync(178_000)
  await result
  expect(signals.every(signal => signal.aborted)).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
})

it('卸载信号取消健康请求及重试等待', async () => {
  vi.mocked(client.fetchHealth).mockResolvedValue(health)
  const controller = new AbortController()
  const waiting = waitForApplicationRestart({ startedAt: health.started_at, serverTime: health.server_time }, undefined, controller.signal)
  const result = expect(waiting).rejects.toMatchObject({ name: 'AbortError' })
  await vi.advanceTimersByTimeAsync(0)
  controller.abort()
  await result
  await vi.advanceTimersByTimeAsync(5_000)
  expect(client.fetchHealth).toHaveBeenCalledTimes(1)
  expect(vi.getTimerCount()).toBe(0)
})

it('记录baseline前验证应用和实例标识', async () => {
  vi.mocked(client.fetchHealth).mockResolvedValue({ ...health, started_at: '' })
  await expect(readRestartHealth()).rejects.toThrow('目标地址未返回阅读器服务状态')
  expect(vi.getTimerCount()).toBe(0)
})

it('相同地址保留页面来源和代理，修改主机端口时构造新地址', () => {
  const settings = { fields: [
    { key: 'host', value: '127.0.0.1', current_value: '127.0.0.1' },
    { key: 'port', value: 8765, current_value: 8765 },
  ] } as ApplicationSettings
  expect(applicationRestartTarget(settings)).toEqual({ origin: undefined, address: window.location.origin })
  settings.fields[0].value = '::1'
  settings.fields[1].value = 8800
  expect(applicationRestartTarget(settings)).toEqual({ origin: 'http://[::1]:8800', address: 'http://[::1]:8800' })
})

it.each(['#application-settings', '#reading-settings'])('同一设置页面 %s 规范目标锚点后显式刷新', hash => {
  const reload = vi.fn()
  const replace = vi.fn()
  const replaceState = vi.fn()
  const state = { key: 'settings-history', idx: 2 }
  vi.stubGlobal('window', {
    location: { href: `http://127.0.0.1:8765/settings/general${hash}`, reload, replace },
    history: { state, replaceState },
  })
  openRestartedSettings('http://127.0.0.1:8765')
  expect(replaceState).toHaveBeenCalledWith(state, '', 'http://127.0.0.1:8765/settings/general#application-settings')
  expect(reload).toHaveBeenCalledTimes(1)
  expect(replace).not.toHaveBeenCalled()
})

it.each([
  ['http://127.0.0.1:8765/settings/general#application-settings', 'http://127.0.0.1:8800'],
  ['http://127.0.0.1:8765/library', 'http://127.0.0.1:8765'],
  ['http://127.0.0.1:8765/settings/general?previous=1', 'http://127.0.0.1:8765'],
])('不同来源、路径或查询使用整页导航：%s → %s', (href, address) => {
  const reload = vi.fn()
  const replace = vi.fn()
  const replaceState = vi.fn()
  vi.stubGlobal('window', { location: { href, reload, replace }, history: { replaceState } })
  openRestartedSettings(address)
  expect(replace).toHaveBeenCalledWith(`${address}/settings/general#application-settings`)
  expect(reload).not.toHaveBeenCalled()
  expect(replaceState).not.toHaveBeenCalled()
})

it('持久化校验只读取实际存储，缺失、旧值和读取失败均报告未保存', async () => {
  const general = await import('../src/settings/preferences')
  const processing = await import('../src/processing/preferences')
  general.updateGeneralSettings({ fontSize: 21 })
  processing.updateProcessingPreferences({ concurrency: 4 })
  const settings = general.getGeneralSettings()
  const preferences = processing.getStoredProcessingPreferences()
  const write = vi.spyOn(Storage.prototype, 'setItem')
  expect(general.generalSettingsPersisted(settings)).toBe(true)
  expect(processing.processingPreferencesPersisted(preferences)).toBe(true)
  expect(general.generalSettingsPersisted({ ...settings, fontSize: 22 })).toBe(false)
  expect(processing.processingPreferencesPersisted({ ...preferences, concurrency: 5 })).toBe(false)
  localStorage.clear()
  expect(general.generalSettingsPersisted(settings)).toBe(false)
  expect(processing.processingPreferencesPersisted(preferences)).toBe(false)
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('存储不可用') })
  expect(general.generalSettingsPersisted(settings)).toBe(false)
  expect(processing.processingPreferencesPersisted(preferences)).toBe(false)
  expect(write).not.toHaveBeenCalled()
})
