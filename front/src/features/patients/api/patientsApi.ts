// import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'
import { mockDelay } from '@/lib/mockDelay'

import { mockDeviceFor } from '@/features/devices/mocks'
import type { HolterHealth } from '@/features/devices/types'

import { MOCK_PATIENTS, mockStudiesFor, mockSummaryFor } from '../mocks'
import type {
  CreatePatientInput,
  Patient,
  PatientListParams,
  PatientListResponse,
  PatientStudiesResponse,
  PatientSummary,
  UpdatePatientInput,
} from '../types'

/**
 * GET /patients — Lista paginada de pacientes con búsqueda y filtros.
 *
 * BACKEND PENDIENTE — ver TES-17.
 * Para activar el endpoint real cuando esté disponible:
 *   1. Descomentar las dos líneas con `api.get(...)` + `return data`.
 *   2. Borrar todo el bloque `// MOCK ↓ ... // MOCK ↑`.
 *   3. Borrar el import de `MOCK_PATIENTS` (y `mockDelay`).
 */
export async function listPatients(params: PatientListParams = {}): Promise<PatientListResponse> {
  // TODO(TES-17 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<PatientListResponse>('/patients', { params })
  // return data

  // MOCK ↓
  await mockDelay()

  const q = params.q?.trim().toLowerCase()
  const statusFilter = params.status && params.status.length > 0 ? new Set(params.status) : null
  const limit = params.limit ?? 20
  const offset = params.offset ?? 0
  const sort = params.sort ?? 'lastDataReceivedAt'
  const order = params.order ?? 'desc'

  let filtered = MOCK_PATIENTS.slice()

  if (q) {
    filtered = filtered.filter((p) => p.fullName.toLowerCase().includes(q) || p.dni.includes(q))
  }

  if (statusFilter) {
    filtered = filtered.filter((p) => statusFilter.has(p.studyStatus))
  }

  filtered.sort((a, b) => {
    let cmp: number
    if (sort === 'name') {
      cmp = a.fullName.localeCompare(b.fullName, 'es')
    } else {
      const aT = a.lastDataReceivedAt ? new Date(a.lastDataReceivedAt).getTime() : 0
      const bT = b.lastDataReceivedAt ? new Date(b.lastDataReceivedAt).getTime() : 0
      cmp = aT - bT
    }
    return order === 'asc' ? cmp : -cmp
  })

  const items = filtered.slice(offset, offset + limit)

  return { items, total: filtered.length, limit, offset }
  // MOCK ↑
}

/**
 * GET /patients/:id — Detalle de un paciente.
 *
 * BACKEND PENDIENTE — ver TES-17.
 */
export async function getPatient(id: string): Promise<Patient> {
  // TODO(TES-17 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<Patient>(`/patients/${id}`)
  // return data

  // MOCK ↓
  await mockDelay()
  const patient = MOCK_PATIENTS.find((p) => p.id === id)
  if (!patient) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  return patient
  // MOCK ↑
}

/**
 * GET /patients/:id/studies — Estudios previos de un paciente.
 *
 * BACKEND PENDIENTE — ver TES-18.
 */
export async function getPatientStudies(id: string): Promise<PatientStudiesResponse> {
  // TODO(TES-18 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<PatientStudiesResponse>(`/patients/${id}/studies`)
  // return data

  // MOCK ↓
  await mockDelay()
  const patientExists = MOCK_PATIENTS.some((p) => p.id === id)
  if (!patientExists) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  const items = mockStudiesFor(id)
  return { items, total: items.length }
  // MOCK ↑
}

/**
 * GET /patients/:id/summary — Métricas resumen sobre la última ventana de monitoreo.
 *
 * BACKEND PENDIENTE — ver TES-20.
 */
export async function getPatientSummary(id: string): Promise<PatientSummary> {
  // TODO(TES-20 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<PatientSummary>(`/patients/${id}/summary`)
  // return data

  // MOCK ↓
  await mockDelay()
  const patientExists = MOCK_PATIENTS.some((p) => p.id === id)
  if (!patientExists) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  return mockSummaryFor(id)
  // MOCK ↑
}

/**
 * GET /patients/:id/device — Salud del dispositivo asignado.
 *
 * BACKEND PENDIENTE — ver TES-19.
 */
export async function getPatientDevice(id: string): Promise<HolterHealth> {
  // TODO(TES-19 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<HolterHealth>(`/patients/${id}/device`)
  // return data

  // MOCK ↓
  await mockDelay()
  const patient = MOCK_PATIENTS.find((p) => p.id === id)
  if (!patient) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  const device = mockDeviceFor(id)
  if (!device) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Este paciente no tiene un dispositivo asignado.',
    })
  }
  return device
  // MOCK ↑
}

/**
 * Genera el próximo id `p_xxx` a partir del máximo sufijo numérico en el mock.
 * Solo se usa en la capa mock; el backend real asigna el id.
 */
function nextMockPatientId(): string {
  const maxN = MOCK_PATIENTS.reduce((max, p) => {
    const n = Number(p.id.replace(/^p_/, ''))
    return Number.isNaN(n) ? max : Math.max(max, n)
  }, 0)
  return `p_${String(maxN + 1).padStart(3, '0')}`
}

/**
 * POST /patients — Alta de un paciente.
 *
 * BACKEND PENDIENTE — ver TES-28.
 * Para activar el endpoint real cuando esté disponible:
 *   1. Descomentar las dos líneas con `api.post(...)` + `return data`.
 *   2. Borrar todo el bloque `// MOCK ↓ ... // MOCK ↑`.
 */
export async function createPatient(input: CreatePatientInput): Promise<Patient> {
  // TODO(TES-28 backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Patient>('/patients', input)
  // return data

  // MOCK ↓
  await mockDelay()
  if (MOCK_PATIENTS.some((p) => p.dni === input.dni)) {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'Ya existe un paciente con ese DNI.',
    })
  }
  const patient: Patient = {
    id: nextMockPatientId(),
    fullName: input.fullName,
    dni: input.dni,
    birthDate: input.birthDate,
    sex: input.sex,
    contactEmail: input.contactEmail,
    contactPhone: input.contactPhone,
    assignedDeviceId: null,
    studyStatus: 'none',
    lastDataReceivedAt: null,
  }
  MOCK_PATIENTS.push(patient)
  return patient
  // MOCK ↑
}

/**
 * PATCH /patients/:id — Edición de un paciente (campos parciales).
 *
 * BACKEND PENDIENTE — ver TES-28.
 */
export async function updatePatient(id: string, input: UpdatePatientInput): Promise<Patient> {
  // TODO(TES-28 backend): descomentar cuando el endpoint exista
  // const { data } = await api.patch<Patient>(`/patients/${id}`, input)
  // return data

  // MOCK ↓
  await mockDelay()
  const patient = MOCK_PATIENTS.find((p) => p.id === id)
  if (!patient) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  if (input.dni && MOCK_PATIENTS.some((p) => p.id !== id && p.dni === input.dni)) {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'Ya existe un paciente con ese DNI.',
    })
  }
  Object.assign(patient, input)
  return patient
  // MOCK ↑
}

/**
 * DELETE /patients/:id — Baja de un paciente.
 *
 * BACKEND PENDIENTE — ver TES-28.
 */
export async function deletePatient(id: string): Promise<void> {
  // TODO(TES-28 backend): descomentar cuando el endpoint exista
  // await api.delete(`/patients/${id}`)
  // return

  // MOCK ↓
  await mockDelay()
  const index = MOCK_PATIENTS.findIndex((p) => p.id === id)
  if (index === -1) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  MOCK_PATIENTS.splice(index, 1)
  // MOCK ↑
}
