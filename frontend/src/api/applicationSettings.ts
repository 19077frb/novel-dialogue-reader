import { apiData } from './client'
import type { components } from './schema'

export type ApplicationSettings = components['schemas']['ApplicationSettingsOut']
export type ApplicationSettingsPatch = components['schemas']['ApplicationSettingsPatch']
export const applicationSettingsKey = ['application-settings'] as const

export function fetchApplicationSettings(signal?: AbortSignal): Promise<ApplicationSettings> {
  return apiData('/api/settings/application', { signal })
}

export function saveApplicationSettings(payload: ApplicationSettingsPatch): Promise<ApplicationSettings> {
  return apiData('/api/settings/application', { method: 'PATCH', body: payload })
}

export function saveAndRestartApplication(payload: ApplicationSettingsPatch): Promise<ApplicationSettings> {
  return apiData('/api/settings/application/save-and-restart', { method: 'POST', body: payload })
}
