/**
 * Alertas clínicas.
 *
 * Diferencia con `features/dashboard/types.ts`: el widget del dashboard mezcla
 * estas alertas con las sintéticas de "equipo sin transmitir", que no existen
 * como fila y no se pueden atender. Acá solo viven las reales — por eso `id` es
 * un UUID y no un string con prefijo.
 */

export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low'

export type AlertKind =
  | 'tachycardia'
  | 'bradycardia'
  | 'afib'
  | 'pvc'
  | 'pause'
  | 'noise'
  | 'symptom_marker'
  | 'other'

export interface Alert {
  id: string
  patientId: string
  patientName: string
  kind: AlertKind
  severity: AlertSeverity
  message: string
  detectedAt: string
  studyId: string | null
  seenAt: string | null
  acknowledgedAt: string | null
  acknowledgedByName: string | null
}

export interface AlertListParams {
  acknowledged?: boolean
  severity?: AlertSeverity[]
  limit?: number
  offset?: number
}

export interface AlertListResponse {
  items: Alert[]
  total: number
  limit: number
  offset: number
  /** Pendientes en total, sin importar filtro ni página: alimenta el badge del menú. */
  pendingTotal: number
}
