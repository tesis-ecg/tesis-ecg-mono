import { api } from '@/lib/api'

import type {
  AlertList,
  Catalogs,
  DeviceStatus,
  PatientProfile,
  PatientReport,
  PushPlatform,
  ReportInput,
  ReportList,
  Session,
} from './types'

export async function login(identifier: string, password: string): Promise<Session> {
  const { data } = await api.post<Session>('/mobile/auth/login', { identifier, password })
  return data
}

export async function refreshAccess(
  refreshToken: string,
): Promise<{ accessToken: string; expiresAt: string }> {
  const { data } = await api.post<{ accessToken: string; expiresAt: string }>(
    '/mobile/auth/refresh',
    { refreshToken },
  )
  return data
}

export async function logout(): Promise<void> {
  await api.post('/mobile/auth/logout')
}

export async function getMe(): Promise<PatientProfile> {
  const { data } = await api.get<PatientProfile>('/mobile/me')
  return data
}

export async function getDevice(): Promise<DeviceStatus> {
  const { data } = await api.get<DeviceStatus>('/mobile/device')
  return data
}

/**
 * Qué avisos pide la app.
 *
 * Espejo de `MobileAlertStatus` del backend. `answered` no es el complemento de
 * `pending`: el aviso de chaleco mal colocado no pide respuesta y solo aparece
 * en `all`.
 */
export type AlertStatus = 'all' | 'pending' | 'answered'

export interface AlertQuery {
  limit?: number
  offset?: number
  status?: AlertStatus
}

export async function getAlerts(query: AlertQuery = {}): Promise<AlertList> {
  const { data } = await api.get<AlertList>('/mobile/alerts', { params: query })
  return data
}

export async function getCatalogs(): Promise<Catalogs> {
  const { data } = await api.get<Catalogs>('/mobile/catalogs')
  return data
}

export async function getReports(limit = 50, offset = 0): Promise<ReportList> {
  const { data } = await api.get<ReportList>('/mobile/reports', { params: { limit, offset } })
  return data
}

export async function getReport(reportId: string): Promise<PatientReport> {
  const { data } = await api.get<PatientReport>(`/mobile/reports/${reportId}`)
  return data
}

export async function createReport(input: ReportInput): Promise<PatientReport> {
  const { data } = await api.post<PatientReport>('/mobile/reports', input)
  return data
}

export async function registerPushToken(token: string, platform: PushPlatform): Promise<void> {
  await api.post('/mobile/push-tokens', { token, platform })
}

/**
 * POST y no DELETE: el token va en el body.
 *
 * Un `ExponentPushToken[...]` en el path o el query string queda escrito en los
 * access logs del hosting y en cualquier proxy del camino.
 */
export async function unregisterPushToken(token: string, platform: PushPlatform): Promise<void> {
  await api.post('/mobile/push-tokens/remove', { token, platform })
}
