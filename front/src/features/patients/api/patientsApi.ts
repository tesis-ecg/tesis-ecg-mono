import { api } from '@/lib/api'

import type { HolterHealth } from '@/features/devices/types'

import type {
  CreatedPatient,
  CreatePatientInput,
  Patient,
  PatientPassword,
  PatientListParams,
  PatientListResponse,
  PatientStudiesResponse,
  PatientSummary,
  UpdatePatientInput,
} from '../types'

export async function listPatients(params: PatientListParams = {}): Promise<PatientListResponse> {
  const { data } = await api.get<PatientListResponse>('/patients', { params })
  return data
}

export async function getPatient(id: string): Promise<Patient> {
  const { data } = await api.get<Patient>(`/patients/${id}`)
  return data
}

export async function getPatientStudies(id: string): Promise<PatientStudiesResponse> {
  const { data } = await api.get<PatientStudiesResponse>(`/patients/${id}/studies`)
  return data
}

export async function getPatientSummary(id: string, windowHours?: number): Promise<PatientSummary> {
  const params = windowHours === undefined ? undefined : { windowHours }
  const { data } = await api.get<PatientSummary>(`/patients/${id}/summary`, { params })
  return data
}

export async function getPatientDevice(id: string): Promise<HolterHealth> {
  const { data } = await api.get<HolterHealth>(`/patients/${id}/device`)
  return data
}

export async function createPatient(input: CreatePatientInput): Promise<CreatedPatient> {
  const { data } = await api.post<CreatedPatient>('/patients', input)
  return data
}

/** Crea el acceso a la app de un paciente cargado antes de que la app existiera. */
export async function createPatientAppAccount(id: string): Promise<PatientPassword> {
  const { data } = await api.post<PatientPassword>(`/patients/${id}/app-account`)
  return data
}

/**
 * Genera una contraseña nueva y la devuelve en claro una sola vez.
 *
 * No existe un endpoint para "ver" la actual porque no existe la actual: Auth0
 * guarda solo el hash.
 */
export async function regeneratePatientAppPassword(id: string): Promise<PatientPassword> {
  const { data } = await api.post<PatientPassword>(`/patients/${id}/app-password`)
  return data
}

/** Dispara el mail de recuperación de Auth0 al paciente. */
export async function sendPatientPasswordReset(id: string): Promise<void> {
  await api.post(`/patients/${id}/password-reset`)
}

export async function updatePatient(id: string, input: UpdatePatientInput): Promise<Patient> {
  const { data } = await api.patch<Patient>(`/patients/${id}`, input)
  return data
}

export async function deletePatient(id: string): Promise<void> {
  await api.delete(`/patients/${id}`)
}
