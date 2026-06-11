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

import type { Study, StudyListParams, StudyListResponse } from './types'

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

/**
 * Agrega todos los estudios de todos los pacientes mock a `Study[]`
 * (denormalizado, igual que `mockStudyFor`), ordenado por inicio desc.
 * Es la fuente del listado `/studies` mientras no exista `GET /studies`.
 */
export function getAllMockStudies(): Study[] {
  return MOCK_PATIENTS.flatMap((patient) =>
    mockStudiesFor(patient.id).map((study) => {
      const startedAtMs = new Date(study.startedAt).getTime()
      const endedAtMs = study.endedAt ? new Date(study.endedAt).getTime() : Date.now()
      return {
        id: study.id,
        patientId: study.patientId,
        patientName: patient.fullName,
        startedAt: study.startedAt,
        endedAt: study.endedAt,
        durationMs: Math.max(0, endedAtMs - startedAtMs),
        deviceSerial: deviceSerialFor(study.deviceId),
        status: study.status,
      } satisfies Study
    }),
  ).sort((a, b) => new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime())
}

/** Aplica búsqueda (`q`), filtro por estado y paginación sobre los estudios mock. */
export function mockStudiesList(params: StudyListParams = {}): StudyListResponse {
  const { q, status, limit = 20, offset = 0 } = params
  let items = getAllMockStudies()

  if (q) {
    const needle = q.toLowerCase()
    items = items.filter(
      (s) =>
        s.patientName.toLowerCase().includes(needle) ||
        s.deviceSerial.toLowerCase().includes(needle) ||
        s.id.toLowerCase().includes(needle),
    )
  }

  if (status && status.length > 0) {
    items = items.filter((s) => status.includes(s.status))
  }

  const total = items.length
  return { items: items.slice(offset, offset + limit), total, limit, offset }
}
