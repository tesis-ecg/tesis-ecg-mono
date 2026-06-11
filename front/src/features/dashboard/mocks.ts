/**
 * Datos mock — borrar este archivo y eliminar los imports en `api/dashboardApi.ts`
 * cuando los endpoints reales del backend estén disponibles
 * (ver TES-33, TES-34, TES-35, TES-36, TES-37).
 */
import { MOCK_PATIENTS, mockStudiesFor } from '@/features/patients/mocks'
import { MOCK_HOLTERS, mockHealthForHolter } from '@/features/devices/mocks'

import type {
  AlertKind,
  AlertSeverity,
  AttentionPatient,
  DashboardAlert,
  DashboardKpis,
  DeviceWatchdogItem,
  DeviceWatchdogReason,
  RunningStudy,
} from './types'

export function getMockDashboardKpis(): DashboardKpis {
  return {
    activePatients: 12,
    pendingAlerts: 3,
    runningStudies: 5,
    activePatientsDelta: { value: 2, trend: 'up' },
    pendingAlertsDelta: { value: -1, trend: 'down' },
    runningStudiesDelta: { value: 0, trend: 'flat' },
  }
}

// --- Widgets de acceso rápido (derivados de los mocks de pacientes/dispositivos) ---

function serialFor(deviceId: string | null): string | null {
  if (!deviceId) return null
  return MOCK_HOLTERS.find((h) => h.id === deviceId)?.serial ?? null
}

/** Pacientes con monitoreo en marcha (los que alimentan alertas/atención/estudios). */
const ACTIVE_PATIENTS = MOCK_PATIENTS.filter(
  (p) => p.studyStatus === 'active' || p.studyStatus === 'paused',
)

const ALERT_KINDS: AlertKind[] = ['afib', 'pause', 'tachycardia', 'bradycardia', 'pvc']
const ALERT_SEVERITIES: AlertSeverity[] = ['critical', 'high', 'high', 'medium', 'low']

export function getMockDashboardAlerts(): DashboardAlert[] {
  const arrhythmia = ACTIVE_PATIENTS.slice(0, 4).map((p, i) => {
    const study = mockStudiesFor(p.id).find((s) => s.status === 'in_progress')
    return {
      id: `al_${String(i + 1).padStart(3, '0')}`,
      patientId: p.id,
      patientName: p.fullName,
      kind: ALERT_KINDS[i % ALERT_KINDS.length],
      severity: ALERT_SEVERITIES[i % ALERT_SEVERITIES.length],
      detectedAt: new Date(Date.now() - (i + 1) * 37 * 60 * 1000).toISOString(),
      studyId: study?.id ?? null,
    } satisfies DashboardAlert
  })

  // Alerta sintética de dispositivo sin transmitir (watchdog).
  const offlinePatient = ACTIVE_PATIENTS[4] ?? ACTIVE_PATIENTS[0]
  const deviceAlert: DashboardAlert = {
    id: 'al_005',
    patientId: offlinePatient.id,
    patientName: offlinePatient.fullName,
    kind: 'device_offline',
    severity: 'medium',
    detectedAt: new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString(),
    studyId: null,
  }

  return [...arrhythmia, deviceAlert]
}

export function getMockAttentionPatients(): AttentionPatient[] {
  return ACTIVE_PATIENTS.map((p) => ({
    id: p.id,
    fullName: p.fullName,
    studyStatus: p.studyStatus,
    lastDataReceivedAt: p.lastDataReceivedAt,
    deviceSerial: serialFor(p.assignedDeviceId),
  }))
    .sort((a, b) => {
      const ta = a.lastDataReceivedAt ? new Date(a.lastDataReceivedAt).getTime() : 0
      const tb = b.lastDataReceivedAt ? new Date(b.lastDataReceivedAt).getTime() : 0
      return tb - ta
    })
    .slice(0, 6)
}

export function getMockRunningStudies(): RunningStudy[] {
  return ACTIVE_PATIENTS.flatMap((p) => {
    const study = mockStudiesFor(p.id).find((s) => s.status === 'in_progress')
    if (!study) return []
    return [
      {
        id: study.id,
        patientName: p.fullName,
        startedAt: study.startedAt,
        durationMs: Math.max(0, Date.now() - new Date(study.startedAt).getTime()),
        deviceSerial: serialFor(study.deviceId) ?? study.deviceId,
      } satisfies RunningStudy,
    ]
  }).slice(0, 6)
}

const STALE_THRESHOLD_H = 10
const LOW_BATTERY_PCT = 45

export function getMockDeviceWatchdog(): DeviceWatchdogItem[] {
  const items: DeviceWatchdogItem[] = []
  for (const holter of MOCK_HOLTERS.filter((h) => h.status === 'assigned')) {
    const health = mockHealthForHolter(holter.id)
    const hoursSince = holter.lastSeenAt
      ? (Date.now() - new Date(holter.lastSeenAt).getTime()) / 3_600_000
      : Infinity
    const battery = health?.batteryPercent ?? null
    const signal = health?.signalQuality ?? null

    let reason: DeviceWatchdogReason | null = null
    if (hoursSince > STALE_THRESHOLD_H) reason = 'offline'
    else if (battery !== null && battery < LOW_BATTERY_PCT) reason = 'low_battery'
    else if (signal === 'poor' || signal === 'none') reason = 'poor_signal'
    if (!reason) continue

    items.push({
      deviceId: holter.id,
      serial: holter.serial,
      status: holter.status,
      batteryPercent: battery,
      signalQuality: signal,
      lastSeenAt: holter.lastSeenAt,
      reason,
    })
  }
  return items.slice(0, 6)
}
