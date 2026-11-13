import { useSyncExternalStore } from 'react'
import { ApiError } from '../api/client'
import type { JobDetailOut } from '../api/types'
import { readJournal, writeJournal, withWorkflowLock } from './journal'

export type ServiceErrorCode = 'RATE_LIMITED' | 'PROVIDER_QUOTA_EXHAUSTED'
export interface ServiceBlock {
  id: string
  profileId: string
  code: ServiceErrorCode
  createdAt: number
  jobId?: string
}
interface ServiceGateState { block: ServiceBlock | null; observedFailures: string[] }
const PREFIX = 'service-gate:'
const STORAGE_PREFIX = `ndr:tasks:v1:${PREFIX}`
const CHANGED = 'ndr:service-gate-changed'
const keyFor = (profileId: string) => `${PREFIX}${encodeURIComponent(profileId)}`

function readState(profileId: string): ServiceGateState {
  const state = readJournal<ServiceGateState>(keyFor(profileId))
  if (!state) return { block: null, observedFailures: [] }
  if (!Array.isArray(state.observedFailures) || state.observedFailures.some(id => typeof id !== 'string')
    || state.block !== null && (!state.block || typeof state.block.id !== 'string'
      || state.block.profileId !== profileId || !serviceCode(state.block.code))) {
    throw new Error('保存的模型服务阻塞记录无法读取，请先检查浏览器存储，勿重复提交任务。')
  }
  return state
}
function serviceCode(value: unknown): ServiceErrorCode | null {
  return value === 'RATE_LIMITED' || value === 'PROVIDER_QUOTA_EXHAUSTED' ? value : null
}
export function getServiceBlock(profileId: string) { return profileId ? readState(profileId).block : null }
export function serviceBlockReason(block: ServiceBlock): string {
  return `${block.code === 'RATE_LIMITED' ? '模型服务限流' : '模型服务额度不足'}，已暂停此配置的新请求。请处理服务问题后，在任务队列中继续排队范围。`
}
export class ServiceBlockedError extends Error {
  constructor(block: ServiceBlock) { super(serviceBlockReason(block)); this.name = 'ServiceBlockedError' }
}
/** Keep this synchronous and immediately before a model-task POST. */
export function assertServiceAvailable(profileId: string) {
  const block = getServiceBlock(profileId)
  if (block) throw new ServiceBlockedError(block)
}
async function recordFailure(profileId: string, code: ServiceErrorCode, failureId: string, jobId?: string) {
  if (!profileId) return null
  return withWorkflowLock(keyFor(profileId), async () => {
    const state = readState(profileId)
    // Acknowledging a block keeps its failure identities, so restoring old jobs
    // cannot recreate that block. A retry has a new provider-run identity.
    if (state.observedFailures.includes(failureId)) return state.block
    const block = { id: crypto.randomUUID(), profileId, code, createdAt: Date.now(), jobId }
    writeJournal(keyFor(profileId), { block, observedFailures: [...state.observedFailures, failureId] })
    window.dispatchEvent(new Event(CHANGED))
    return block
  })
}
export async function observeServiceError(profileId: string, error: unknown) {
  if (!(error instanceof ApiError)) return null
  const code = serviceCode(error.code)
    ?? (error.status === 429 ? 'RATE_LIMITED' : error.status === 402 ? 'PROVIDER_QUOTA_EXHAUSTED' : null)
  return code ? recordFailure(profileId, code, crypto.randomUUID()) : null
}
export async function submitServiceJob(profileId: string, submit: () => Promise<JobDetailOut>) {
  assertServiceAvailable(profileId)
  try { return await submit() }
  catch (error) {
    const block = await observeServiceError(profileId, error)
    if (block) throw new ServiceBlockedError(block)
    throw error
  }
}
export function jobServiceErrorCode(job: JobDetailOut): ServiceErrorCode | null {
  // Explicit null from the current API overrides any stale human-readable text.
  if ('service_error_code' in job) return serviceCode(job.service_error_code)
  return serviceCode(/^(RATE_LIMITED|PROVIDER_QUOTA_EXHAUSTED):/.exec(job.last_error ?? '')?.[1])
}
export async function observeServiceJob(profileId: string, job: JobDetailOut) {
  // An automatic bounded retry may still resolve a failed attempt in this job.
  if (['QUEUED', 'RUNNING', 'PAUSING'].includes(job.state)) return null
  const code = jobServiceErrorCode(job)
  if (!code) return null
  const failureId = job.service_error_run_id ?? `legacy:${job.id}:${job.updated_at}:${code}`
  return recordFailure(profileId, code, failureId, job.id)
}
export async function clearServiceBlock(profileId: string, expectedId: string) {
  return withWorkflowLock(keyFor(profileId), async () => {
    const state = readState(profileId)
    if (!state.block) return
    if (state.block.id !== expectedId) throw new Error('模型服务又报告了新的问题，请查看最新提示后再继续。')
    writeJournal(keyFor(profileId), { ...state, block: null })
    window.dispatchEvent(new Event(CHANGED))
  })
}

interface ServiceGateStatus { blocks: ServiceBlock[]; errors: string[] }
let cachedSource = ''
let cachedStatus: ServiceGateStatus = { blocks: [], errors: [] }
export function getServiceGateStatus(): ServiceGateStatus {
  let keys: string[]
  let source: string
  try {
    keys = Object.keys(localStorage).filter(key => key.startsWith(STORAGE_PREFIX)).sort()
    source = JSON.stringify(keys.map(key => [key, localStorage.getItem(key)]))
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    source = `unavailable:${message}`
    if (cachedSource !== source) { cachedStatus = { blocks: [], errors: [message] }; cachedSource = source }
    return cachedStatus
  }
  if (cachedSource !== source) {
    const errors: string[] = []
    const blocks = keys.flatMap(key => {
      try {
        const state = readState(decodeURIComponent(key.slice(STORAGE_PREFIX.length)))
        return state.block ? [state.block] : []
      } catch (error) {
        errors.push(error instanceof Error ? error.message : String(error))
        return []
      }
    })
    cachedStatus = { blocks, errors }
    cachedSource = source
  }
  return cachedStatus
}
export function getServiceBlocks() { return getServiceGateStatus().blocks }
function subscribeServiceGate(listener: () => void) {
  const stored = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(STORAGE_PREFIX)) listener() }
  window.addEventListener('storage', stored)
  window.addEventListener(CHANGED, listener)
  return () => { window.removeEventListener('storage', stored); window.removeEventListener(CHANGED, listener) }
}
export function useServiceGateStatus() { return useSyncExternalStore(subscribeServiceGate, getServiceGateStatus) }
export function useServiceBlocks() {
  return useSyncExternalStore(subscribeServiceGate, getServiceBlocks)
}
