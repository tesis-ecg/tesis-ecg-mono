/**
 * Datos mock — borrar este archivo y eliminar imports en `api/devicesApi.ts`
 * cuando los endpoints reales del backend estén disponibles.
 */
import { MOCK_PATIENTS } from '@/features/patients/mocks'

import type { Holter, HolterHealth, HolterStatus } from './types'

function hoursAgoISO(hours: number): string {
  return new Date(Date.now() - hours * 60 * 60 * 1000).toISOString()
}

function daysAgoISO(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
}

function serialOf(deviceId: string): string {
  return `HOLTER-AR-${deviceId.replace('d_', '')}`
}

/**
 * Construye un Holter base con valores deterministas a partir del índice.
 * `status`, `assignedPatientId`, `lastSeenAt` se ajustan luego según el seed.
 */
function buildHolter(i: number, status: HolterStatus = 'available', hasReported = true): Holter {
  const id = `d_${String(i).padStart(3, '0')}`
  return {
    id,
    serial: serialOf(id),
    model: 'SIM7080G v1',
    firmwareVersion: '0.3.1',
    status,
    assignedPatientId: null,
    lastSeenAt: hasReported ? hoursAgoISO((i % 24) + 0.25) : null,
    createdAt: daysAgoISO(i + 7),
  }
}

const ASSIGNED_IDS = new Set(
  MOCK_PATIENTS.map((p) => p.assignedDeviceId).filter((d): d is string => d !== null),
)

// IDs de los Holters asignados según los pacientes seed (d_001..d_025 omitiendo los `none`).
const assignedHolters: Holter[] = Array.from(ASSIGNED_IDS)
  .sort()
  .map((id) => {
    const patient = MOCK_PATIENTS.find((p) => p.assignedDeviceId === id)!
    const i = Number(id.replace('d_', ''))
    return {
      id,
      serial: serialOf(id),
      model: 'SIM7080G v1',
      firmwareVersion: '0.3.1',
      status: 'assigned' as const,
      assignedPatientId: patient.id,
      lastSeenAt: hoursAgoISO((i % 24) + 0.25),
      createdAt: daysAgoISO(i + 30),
    }
  })

// Pool extra: 8 disponibles (6 reportando, 2 nuevos sin último-ping), 2 en mantenimiento, 1 retirado.
// Empieza después del último id usado por pacientes.
const lastAssignedIndex = Math.max(
  ...Array.from(ASSIGNED_IDS).map((d) => Number(d.replace('d_', ''))),
  0,
)
const extraHolters: Holter[] = [
  ...Array.from({ length: 6 }, (_, k) => buildHolter(lastAssignedIndex + 1 + k, 'available', true)),
  // 2 holters recién creados sin ping todavía — para testear el placeholder del detail page
  buildHolter(lastAssignedIndex + 7, 'available', false),
  buildHolter(lastAssignedIndex + 8, 'available', false),
  buildHolter(lastAssignedIndex + 9, 'maintenance', true),
  buildHolter(lastAssignedIndex + 10, 'maintenance', true),
  buildHolter(lastAssignedIndex + 11, 'retired', false),
]

export const MOCK_HOLTERS: Holter[] = [...assignedHolters, ...extraHolters]

/**
 * Devuelve el estado de salud de un Holter por su id. Es la fuente de verdad
 * para todo el dashboard. `lastSeenAt === null` significa que nunca reportó
 * (devolvemos null y el FE muestra el placeholder "no se conectó todavía").
 */
export function mockHealthForHolter(holterId: string): HolterHealth | null {
  const holter = MOCK_HOLTERS.find((h) => h.id === holterId)
  if (!holter || holter.lastSeenAt === null) return null
  const seed = Number(holter.id.slice(2)) || 1
  const batteryPercent = 30 + ((seed * 11) % 70)
  const signalDbm = -60 - ((seed * 7) % 40)
  const signalQuality =
    signalDbm > -70 ? 'good' : signalDbm > -85 ? 'fair' : signalDbm > -100 ? 'poor' : 'none'
  return {
    deviceId: holter.id,
    serial: holter.serial,
    model: holter.model,
    firmwareVersion: holter.firmwareVersion ?? '0.0.0',
    batteryPercent,
    signalDbm,
    signalQuality,
    lastPingAt: holter.lastSeenAt,
    nextScheduledUploadAt: new Date(Date.now() + 35 * 60 * 1000).toISOString(),
    uploadsToday: 13,
    storageUsedMb: 2.7,
    storageTotalMb: 128,
  }
}

/**
 * Wrapper retro-compatible: la API `getPatientDevice` busca al paciente y devuelve
 * la salud del Holter que tiene asignado.
 */
export function mockDeviceFor(patientId: string): HolterHealth | null {
  const patient = MOCK_PATIENTS.find((p) => p.id === patientId)
  if (!patient || !patient.assignedDeviceId) return null
  return mockHealthForHolter(patient.assignedDeviceId)
}

/**
 * Genera el próximo id `d_xxx` a partir del máximo sufijo numérico en el mock.
 * Solo se usa en la capa mock; el backend real asigna el id.
 */
export function nextMockHolterId(): string {
  const maxN = MOCK_HOLTERS.reduce((max, h) => {
    const n = Number(h.id.replace(/^d_/, ''))
    return Number.isNaN(n) ? max : Math.max(max, n)
  }, 0)
  return `d_${String(maxN + 1).padStart(3, '0')}`
}
