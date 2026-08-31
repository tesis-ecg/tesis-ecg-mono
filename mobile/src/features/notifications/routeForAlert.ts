import type { PatientAlert } from '@/features/patient/types'

export type AlertRoute =
  | { pathname: '/report'; params: { alertId: string; occurredAt: string } }
  | { pathname: '/report-response'; params: { reportId: string } }
  | { pathname: '/(tabs)/device' }
  | null

/** Destino de una fila del centro de avisos. */
export function routeForAlert(alert: PatientAlert): AlertRoute {
  if (alert.needsReport) {
    return {
      pathname: '/report',
      params: { alertId: alert.id, occurredAt: alert.detectedAt },
    }
  }
  if (alert.requiresResponse && alert.reportId) {
    return { pathname: '/report-response', params: { reportId: alert.reportId } }
  }
  if (alert.kind === 'vest_misplaced') return { pathname: '/(tabs)/device' }
  return null
}
