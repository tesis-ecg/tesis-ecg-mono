// import { api } from '@/lib/api'
import { MOCK_PATIENTS } from '@/features/patients/mocks'
import { createApiError } from '@/lib/apiError'
import { mockDelay } from '@/lib/mockDelay'

import { MOCK_HOLTERS, mockHealthForHolter, nextMockHolterId } from '../mocks'
import type {
  AssignHolterInput,
  CreateHolterInput,
  Holter,
  HolterHealth,
  HolterListParams,
  HolterListResponse,
  ReassignHolterInput,
  UpdateHolterInput,
} from '../types'

/**
 * GET /devices — Lista paginada de Holters con búsqueda y filtros.
 *
 * BACKEND PENDIENTE — endpoints ABM Holters.
 * Para activar el endpoint real cuando esté disponible:
 *   1. Descomentar las dos líneas con `api.get(...)` + `return data`.
 *   2. Borrar todo el bloque `// MOCK ↓ ... // MOCK ↑`.
 */
export async function listHolters(params: HolterListParams = {}): Promise<HolterListResponse> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<HolterListResponse>('/devices', { params })
  // return data

  // MOCK ↓
  await mockDelay()

  const q = params.q?.trim().toLowerCase()
  const statusFilter = params.status && params.status.length > 0 ? new Set(params.status) : null
  const limit = params.limit ?? 20
  const offset = params.offset ?? 0

  let filtered = MOCK_HOLTERS.slice()

  if (q) {
    filtered = filtered.filter(
      (h) => h.serial.toLowerCase().includes(q) || h.model.toLowerCase().includes(q),
    )
  }

  if (statusFilter) {
    filtered = filtered.filter((h) => statusFilter.has(h.status))
  }

  filtered.sort((a, b) => a.serial.localeCompare(b.serial))

  const items = filtered.slice(offset, offset + limit)

  return { items, total: filtered.length, limit, offset }
  // MOCK ↑
}

/**
 * GET /devices/:id — Detalle de un Holter.
 */
export async function getHolter(id: string): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<Holter>(`/devices/${id}`)
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === id)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  return holter
  // MOCK ↑
}

/**
 * POST /devices — Alta de un Holter.
 */
export async function createHolter(input: CreateHolterInput): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Holter>('/devices', input)
  // return data

  // MOCK ↓
  await mockDelay()
  if (MOCK_HOLTERS.some((h) => h.serial === input.serial)) {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'Ya existe un Holter con ese serial.',
    })
  }
  const holter: Holter = {
    id: nextMockHolterId(),
    serial: input.serial,
    model: input.model,
    firmwareVersion: input.firmwareVersion,
    status: 'available',
    assignedPatientId: null,
    lastSeenAt: null,
    createdAt: new Date().toISOString(),
  }
  MOCK_HOLTERS.push(holter)
  return holter
  // MOCK ↑
}

/**
 * PATCH /devices/:id — Edición parcial de un Holter.
 *
 * Validaciones server-side: 409 si se intenta cambiar `status` mientras el
 * Holter está `assigned`; el FE deshabilita el campo, pero el backend lo refuerza.
 */
export async function updateHolter(id: string, input: UpdateHolterInput): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.patch<Holter>(`/devices/${id}`, input)
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === id)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  if (input.status !== undefined && holter.status === 'assigned') {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'No se puede cambiar el estado mientras el Holter esté asignado. Desasigná primero.',
    })
  }
  if (input.status === 'retired') {
    throw createApiError({
      status: 400,
      code: 'VALIDATION',
      message: 'El estado "retirado" se setea eliminando el Holter, no editándolo.',
    })
  }
  if (input.model !== undefined) holter.model = input.model
  if (input.firmwareVersion !== undefined) holter.firmwareVersion = input.firmwareVersion
  if (input.status !== undefined) holter.status = input.status
  return holter
  // MOCK ↑
}

/**
 * DELETE /devices/:id — Soft-delete: pasa `status` a `retired`.
 *
 * Si el Holter estaba asignado, se desasigna del paciente en la misma operación
 * (idéntico a `unassignHolter`).
 */
export async function deleteHolter(id: string): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.delete<Holter>(`/devices/${id}`)
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === id)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  if (holter.assignedPatientId) {
    const patient = MOCK_PATIENTS.find((p) => p.id === holter.assignedPatientId)
    if (patient) patient.assignedDeviceId = null
  }
  holter.status = 'retired'
  holter.assignedPatientId = null
  return holter
  // MOCK ↑
}

/**
 * POST /devices/:id/reassign — Cambia el paciente asignado a un Holter de
 * forma atómica.
 */
export async function reassignHolter(input: ReassignHolterInput): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Holter>(`/devices/${input.holterId}/reassign`, { patientId: input.newPatientId })
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === input.holterId)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  if (holter.status === 'retired') {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'No se puede reasignar un Holter retirado.',
    })
  }
  const newPatient = MOCK_PATIENTS.find((p) => p.id === input.newPatientId)
  if (!newPatient) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  if (holter.assignedPatientId === newPatient.id) {
    throw createApiError({
      status: 400,
      code: 'VALIDATION',
      message: 'Ese paciente ya tiene este Holter.',
    })
  }
  if (newPatient.assignedDeviceId !== null) {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'El paciente destino ya tiene un Holter asignado.',
    })
  }
  if (holter.assignedPatientId) {
    const oldPatient = MOCK_PATIENTS.find((p) => p.id === holter.assignedPatientId)
    if (oldPatient) oldPatient.assignedDeviceId = null
  }
  holter.status = 'assigned'
  holter.assignedPatientId = newPatient.id
  newPatient.assignedDeviceId = holter.id
  return holter
  // MOCK ↑
}

/**
 * GET /devices/:id/health — Estado de salud del Holter independiente del paciente.
 */
export async function getHolterHealth(id: string): Promise<HolterHealth> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<HolterHealth>(`/devices/${id}/health`)
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === id)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  const health = mockHealthForHolter(id)
  if (!health) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Este Holter no se ha conectado todavía.',
    })
  }
  return health
  // MOCK ↑
}

/**
 * POST /devices/:id/assign — Asigna un Holter a un paciente.
 *
 * Muta tanto el Holter (status='assigned', assignedPatientId) como el paciente
 * (assignedDeviceId). El backend real expone esto como una operación atómica.
 */
export async function assignHolter(input: AssignHolterInput): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Holter>(`/devices/${input.holterId}/assign`, { patientId: input.patientId })
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === input.holterId)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  if (holter.status !== 'available') {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'Este Holter no está disponible para asignación.',
    })
  }
  const patient = MOCK_PATIENTS.find((p) => p.id === input.patientId)
  if (!patient) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Paciente no encontrado.',
    })
  }
  if (patient.assignedDeviceId !== null) {
    throw createApiError({
      status: 409,
      code: 'CONFLICT',
      message: 'El paciente ya tiene un Holter asignado.',
    })
  }
  holter.status = 'assigned'
  holter.assignedPatientId = patient.id
  patient.assignedDeviceId = holter.id
  return holter
  // MOCK ↑
}

/**
 * POST /devices/:id/unassign — Desasigna un Holter del paciente actual.
 */
export async function unassignHolter(holterId: string): Promise<Holter> {
  // TODO(backend): descomentar cuando el endpoint exista
  // const { data } = await api.post<Holter>(`/devices/${holterId}/unassign`)
  // return data

  // MOCK ↓
  await mockDelay()
  const holter = MOCK_HOLTERS.find((h) => h.id === holterId)
  if (!holter) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Holter no encontrado.',
    })
  }
  if (holter.assignedPatientId) {
    const patient = MOCK_PATIENTS.find((p) => p.id === holter.assignedPatientId)
    if (patient) patient.assignedDeviceId = null
  }
  holter.status = 'available'
  holter.assignedPatientId = null
  return holter
  // MOCK ↑
}
