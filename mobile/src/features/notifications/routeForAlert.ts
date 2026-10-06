import type { AlertSeverity, PatientAlert } from '@/features/patient/types'

export type AlertRoute =
  | {
      pathname: '/report'
      params: { alertId: string; occurredAt: string; kind: string; severity: AlertSeverity }
    }
  | { pathname: '/report-response'; params: { reportId: string } }
  | { pathname: '/(tabs)/device' }
  | null

/** Destino de una fila del centro de avisos. */
export function routeForAlert(alert: PatientAlert): AlertRoute {
  if (alert.needsReport) {
    return {
      pathname: '/report',
      // El `kind` y la severidad salen del aviso que ya está en pantalla:
      // entrando por acá no hace falta pedirle nada más al backend para
      // encabezar el formulario con su nombre, su ícono y su color.
      params: {
        alertId: alert.id,
        occurredAt: alert.detectedAt,
        kind: alert.kind,
        severity: alert.severity,
      },
    }
  }
  if (alert.requiresResponse && alert.reportId) {
    return { pathname: '/report-response', params: { reportId: alert.reportId } }
  }
  if (['vest_misplaced', 'battery_low', 'battery_critical'].includes(alert.kind)) {
    return { pathname: '/(tabs)/device' }
  }
  return null
}
