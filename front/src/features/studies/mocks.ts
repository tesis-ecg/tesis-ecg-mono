/**
 * Datos mock — borrar este archivo y eliminar imports en `api/studiesApi.ts`
 * cuando el endpoint `GET /studies/:id` esté disponible (ver TES-32).
 *
 * El generador deriva la metadata del id del estudio (`s_<patientId>_<n>`) y
 * busca el paciente en `features/patients/mocks` para mantener consistencia
 * con la tabla `<StudiesTable />`. Si el id apunta a un estudio "1" o existe
 * en `mockStudiesFor`, devolvemos los datos exactos de esa tabla — así el
 * detalle del estudio coincide con la fila que el médico clickeó.
 */
import { MOCK_PATIENTS, mockStudiesFor } from '@/features/patients/mocks'

import type { Study } from './types'

export function mockStudyFor(id: string): Study | null {
  // Parsear el id `s_<patientId>_<n>`. El patientId es `p_xxx`.
  const match = /^s_(p_\d+)_(\d+)$/.exec(id)
  if (!match) return null
  const [, patientId] = match

  const patient = MOCK_PATIENTS.find((p) => p.id === patientId)
  if (!patient) return null

  const study = mockStudiesFor(patientId).find((s) => s.id === id)
  if (!study) return null

  const startedAtMs = new Date(study.startedAt).getTime()
  const endedAtMs = study.endedAt ? new Date(study.endedAt).getTime() : Date.now()
  const durationMs = Math.max(0, endedAtMs - startedAtMs)

  return {
    id: study.id,
    patientId: study.patientId,
    patientName: patient.fullName,
    startedAt: study.startedAt,
    endedAt: study.endedAt,
    durationMs,
    deviceSerial: deviceSerialFor(study.deviceId),
    status: study.status,
  }
}

/**
 * Genera un serial de Holter "humano" a partir del device id `d_xxx`. Match
 * con la convención usada por `features/devices/mocks.ts`.
 */
function deviceSerialFor(deviceId: string): string {
  const suffix = deviceId.replace(/^d_/, '')
  return `HOL-${suffix}`
}
