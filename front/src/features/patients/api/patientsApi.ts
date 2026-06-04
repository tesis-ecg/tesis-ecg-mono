import { api } from '@/lib/api'

import type { HolterHealth } from '@/features/devices/types'

import type {
  CreatePatientInput,
  Patient,
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

export async function getPatientSummary(
  id: string,
  windowHours?: number,
): Promise<PatientSummary> {
  const params = windowHours === undefined ? undefined : { windowHours }
  const { data } = await api.get<PatientSummary>(`/patients/${id}/summary`, { params })
  return data
}

export async function getPatientDevice(id: string): Promise<HolterHealth> {
  const { data } = await api.get<HolterHealth>(`/patients/${id}/device`)
  return data
}

export async function createPatient(input: CreatePatientInput): Promise<Patient> {
  const { data } = await api.post<Patient>('/patients', input)
  return data
}

export async function updatePatient(id: string, input: UpdatePatientInput): Promise<Patient> {
  const { data } = await api.patch<Patient>(`/patients/${id}`, input)
  return data
}

export async function deletePatient(id: string): Promise<void> {
  await api.delete(`/patients/${id}`)
}
