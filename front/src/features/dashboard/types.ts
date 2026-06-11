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
