/** 模型配置的查询与写操作（T06）。密钥只出现在创建/替换请求里，绝不进入查询缓存以外的状态。 */
import { apiData } from './client'
import type {
  ModelProfileCreate,
  ModelProfileOut,
  ModelProfilePatch,
  ProtocolCapabilitiesOut,
} from './types'

export const profileKeys = {
  profiles: () => ['model-profiles'] as const,
  protocols: () => ['model-profiles', 'protocols'] as const,
}

export function fetchProfiles(signal?: AbortSignal): Promise<ModelProfileOut[]> {
  return apiData<ModelProfileOut[]>('/api/model-profiles', { signal })
}

export function fetchProtocols(signal?: AbortSignal): Promise<ProtocolCapabilitiesOut[]> {
  return apiData<ProtocolCapabilitiesOut[]>('/api/model-profiles/protocols', { signal })
}

export function createProfile(
  payload: ModelProfileCreate,
  signal?: AbortSignal,
): Promise<ModelProfileOut> {
  return apiData<ModelProfileOut>('/api/model-profiles', {
    method: 'POST',
    body: payload,
    signal,
  })
}

export function updateProfile(
  profileId: string,
  payload: ModelProfilePatch,
  signal?: AbortSignal,
): Promise<ModelProfileOut> {
  return apiData<ModelProfileOut>(`/api/model-profiles/${profileId}`, {
    method: 'PATCH',
    body: payload,
    signal,
  })
}

export async function deleteProfile(profileId: string, signal?: AbortSignal): Promise<void> {
  const response = await fetch(`/api/model-profiles/${profileId}`, {
    method: 'DELETE',
    signal,
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    const message = body?.error?.message ?? `删除失败（HTTP ${response.status}）`
    throw new Error(message)
  }
}