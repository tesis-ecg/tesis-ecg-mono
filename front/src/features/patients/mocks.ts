/**
 * Datos mock — borrar este archivo y eliminar imports en `api/patientsApi.ts`
 * cuando los endpoints reales del backend estén disponibles (ver TES-17, TES-18, TES-19).
 */
import type {
  Patient,
  PatientDevice,
  PatientStudy,
  PatientStudySessionStatus,
  PatientStudyStatus,
  PatientSummary,
} from './types'

const FIRST_NAMES = [
  'María',
  'Juan',
  'Carlos',
  'Lucía',
  'Sofía',
  'Diego',
  'Valentina',
  'Mateo',
  'Camila',
  'Federico',
  'Florencia',
  'Tomás',
  'Martina',
  'Bautista',
  'Agustina',
  'Joaquín',
  'Isabella',
  'Santiago',
  'Emma',
  'Nicolás',
  'Renata',
  'Lautaro',
  'Mía',
  'Benjamín',
  'Olivia',
]

const LAST_NAMES = [
  'García',
  'Rodríguez',
  'González',
  'Fernández',
  'López',
  'Martínez',
  'Pérez',
  'Gómez',
  'Sánchez',
  'Díaz',
  'Romero',
  'Suárez',
  'Acosta',
  'Álvarez',
  'Benítez',
  'Castro',
  'Domínguez',
  'Ferrari',
  'Herrera',
  'Iglesias',
  'Juárez',
  'Méndez',
  'Navarro',
  'Ortiz',
  'Quiroga',
]

const STATUSES: PatientStudyStatus[] = ['active', 'completed', 'paused', 'none']

function hoursAgoISO(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString()
}

/**
 * Fecha de nacimiento determinística por índice, con edades entre ~25 y ~80
 * años. Formato ISO `YYYY-MM-DD`.
 */
function birthDateFor(i: number): string {
  const age = 25 + ((i * 13) % 56) // 25..80
  const month = ((i * 5) % 12) + 1
  const day = ((i * 7) % 28) + 1
  const year = new Date().getFullYear() - age
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

function pickStatus(i: number): PatientStudyStatus {
  return STATUSES[i % STATUSES.length]
}

function generatePatient(i: number): Patient {
  const first = FIRST_NAMES[i % FIRST_NAMES.length]
  const last = LAST_NAMES[(i * 7) % LAST_NAMES.length]
  const status = pickStatus(i)
  const dni = String(28_000_000 + i * 137_521).padStart(8, '0')
  return {
    id: `p_${String(i + 1).padStart(3, '0')}`,
    fullName: `${first} ${last}`,
    dni,
    birthDate: birthDateFor(i),
    sex: i % 2 === 0 ? 'F' : 'M',
    assignedDeviceId: status === 'none' ? null : `d_${String((i % 12) + 1).padStart(3, '0')}`,
    studyStatus: status,
    lastDataReceivedAt:
      status === 'active'
        ? hoursAgoISO((i % 6) + 0.5)
        : status === 'paused'
          ? hoursAgoISO(12 + (i % 24))
          : status === 'completed'
            ? hoursAgoISO(72 + (i % 200))
            : null,
    contactEmail: `${first.toLowerCase()}.${last.toLowerCase().replace('á', 'a').replace('í', 'i').replace('ó', 'o').replace('ú', 'u').replace('é', 'e')}@example.com`,
    contactPhone: `+54 9 11 ${String(4000_0000 + i * 137_521).slice(0, 4)}-${String(i * 13)
      .padStart(4, '0')
      .slice(-4)}`,
  }
}

export const MOCK_PATIENTS: Patient[] = Array.from({ length: 25 }, (_, i) => generatePatient(i))

const STUDY_STATUSES: PatientStudySessionStatus[] = [
  'completed',
  'completed',
  'completed',
  'in_progress',
  'cancelled',
]

export function mockStudiesFor(patientId: string): PatientStudy[] {
  const patient = MOCK_PATIENTS.find((p) => p.id === patientId)
  if (!patient || patient.studyStatus === 'none') return []
  const count = patient.studyStatus === 'active' ? 4 : 2
  return Array.from({ length: count }, (_, i) => {
    const status =
      i === 0 && patient.studyStatus === 'active'
        ? 'in_progress'
        : STUDY_STATUSES[i % STUDY_STATUSES.length]
    const startedAt = hoursAgoISO(24 * (i + 1) * 7)
    const endedAt = status === 'in_progress' ? null : hoursAgoISO(24 * (i + 1) * 7 - 48)
    return {
      id: `s_${patientId}_${i + 1}`,
      patientId,
      startedAt,
      endedAt,
      durationHours: endedAt ? 48 : null,
      status,
      deviceId: patient.assignedDeviceId ?? 'd_001',
      samplesCount: 432_000 + i * 12_000,
      eventsCount: (i + 1) * 2,
    }
  })
}

export function mockSummaryFor(patientId: string): PatientSummary {
  const patient = MOCK_PATIENTS.find((p) => p.id === patientId)
  if (!patient || patient.studyStatus === 'none') {
    return { windowHours: 24, heartRate: null, eventsDetected: null, adherencePercent: null }
  }
  const seed = Number(patient.id.slice(2))
  const avgBpm = 62 + (seed % 24)
  const deltaBpm = ((seed * 3) % 7) - 3
  const events = (seed * 2) % 9
  const eventsDelta = (seed % 5) - 2
  const adherence = 78 + (seed % 22)
  const adherenceDelta = ((seed * 7) % 7) - 3
  const trend = (delta: number) => (delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat')
  return {
    windowHours: 24,
    heartRate: { averageBpm: avgBpm, deltaBpm, trend: trend(deltaBpm) },
    eventsDetected: { count: events, delta: eventsDelta, trend: trend(eventsDelta) },
    adherencePercent: { value: adherence, deltaPp: adherenceDelta, trend: trend(adherenceDelta) },
  }
}

export function mockDeviceFor(patientId: string): PatientDevice | null {
  const patient = MOCK_PATIENTS.find((p) => p.id === patientId)
  if (!patient || !patient.assignedDeviceId) return null
  const batteryPercent = 30 + ((Number(patient.id.slice(2)) * 11) % 70)
  const signalDbm = -60 - ((Number(patient.id.slice(2)) * 7) % 40)
  const signalQuality =
    signalDbm > -70 ? 'good' : signalDbm > -85 ? 'fair' : signalDbm > -100 ? 'poor' : 'none'
  return {
    deviceId: patient.assignedDeviceId,
    serial: `HOLTER-AR-${patient.assignedDeviceId.replace('d_', '')}`,
    model: 'SIM7080G v1',
    firmwareVersion: '0.3.1',
    batteryPercent,
    signalDbm,
    signalQuality,
    lastPingAt: hoursAgoISO(0.25),
    nextScheduledUploadAt: new Date(Date.now() + 35 * 60 * 1000).toISOString(),
    uploadsToday: 13,
    storageUsedMb: 2.7,
    storageTotalMb: 128,
  }
}
