import type { PatientStudyStatus } from '@/features/patients/types'
import type { HolterSignalQuality, HolterStatus } from '@/features/devices/types'

export type KpiTrend = 'up' | 'down' | 'flat'

export interface KpiDelta {
  /** Cambio con signo respecto del período anterior. */
  value: number
  trend: KpiTrend
}

export interface DashboardKpis {
  activePatients: number
  pendingAlerts: number
  runningStudies: number
  /** Deltas opcionales vs período anterior (el backend puede omitirlos). */
  activePatientsDelta?: KpiDelta
  pendingAlertsDelta?: KpiDelta
  runningStudiesDelta?: KpiDelta
}

// --- Widgets de acceso rápido ---

export type AlertSeverity = 'low' | 'medium' | 'high' | 'critical'

export type AlertKind =
  | 'tachycardia'
  | 'bradycardia'
  | 'afib'
  | 'pvc'
  | 'pause'
  | 'noise'
  | 'symptom_marker'
  | 'other'
  | 'device_offline'

export interface DashboardAlert {
  id: string
  patientId: string
  patientName: string
  kind: AlertKind
  severity: AlertSeverity
  detectedAt: string
  /** Estudio asociado para navegar al viewer; null en alertas de dispositivo. */
  studyId: string | null
}

export interface AttentionPatient {
  id: string
  fullName: string
  studyStatus: PatientStudyStatus
  lastDataReceivedAt: string | null
  deviceSerial: string | null
}

export interface RunningStudy {
  id: string
  patientName: string
  startedAt: string
  durationMs: number
  deviceSerial: string
}

export type DeviceWatchdogReason = 'offline' | 'low_battery' | 'poor_signal'

export interface DeviceWatchdogItem {
  deviceId: string
  serial: string
  status: HolterStatus
  batteryPercent: number | null
  signalQuality: HolterSignalQuality | null
  lastSeenAt: string | null
  reason: DeviceWatchdogReason
}

// --- Actividad (lo que alimenta los gráficos de la home) ---

export interface ActivityPoint {
  /** `YYYY-MM-DD`. Siempre vienen 7, con ceros donde no pasó nada. */
  date: string
  alerts: number
  reports: number
  studies: number
}

/**
 * Flujo de los últimos 7 días contra los 7 anteriores.
 *
 * Es flujo y no stock: `current` es cuántas alertas *entraron* esta semana, no
 * cuántas quedan pendientes. Por eso la tarjeta lo rotula "últimos 7 días" en
 * vez de presentarlo como un delta del número grande.
 */
export interface ActivityTrend {
  current: number
  previous: number
}

export interface SeverityBucket {
  severity: AlertSeverity
  count: number
}

export interface FleetHealth {
  assigned: number
  transmitting: number
}

export interface DashboardActivity {
  days: ActivityPoint[]
  alertsTrend: ActivityTrend
  studiesTrend: ActivityTrend
  patientsTrend: ActivityTrend
  pendingBySeverity: SeverityBucket[]
  fleet: FleetHealth
}

export interface DashboardOverview {
  kpis: DashboardKpis
  alerts: DashboardAlert[]
  attentionPatients: AttentionPatient[]
  runningStudies: RunningStudy[]
  deviceWatchdog: DeviceWatchdogItem[]
  activity: DashboardActivity
}
