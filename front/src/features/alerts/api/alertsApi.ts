import { api } from '@/lib/api'

import type { Alert, AlertListParams, AlertListResponse } from '../types'

export async function listAlerts(params: AlertListParams = {}): Promise<AlertListResponse> {
  const { data } = await api.get<AlertListResponse>('/alerts', { params })
  return data
}

/**
 * Deja constancia de que la alerta fue revisada.
 *
 * Responde 409 `ALERT_ALREADY_ACKNOWLEDGED` si otro médico llegó primero: el
 * backend no pisa quién la vio antes.
 */
export async function acknowledgeAlert(id: string): Promise<Alert> {
  const { data } = await api.post<Alert>(`/alerts/${id}/acknowledge`)
  return data
}
