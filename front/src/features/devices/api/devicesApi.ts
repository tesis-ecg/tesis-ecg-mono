import { api } from '@/lib/api'

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

export async function listHolters(params: HolterListParams = {}): Promise<HolterListResponse> {
  const { data } = await api.get<HolterListResponse>('/devices', { params })
  return data
}

export async function getHolter(id: string): Promise<Holter> {
  const { data } = await api.get<Holter>(`/devices/${id}`)
  return data
}

export async function createHolter(input: CreateHolterInput): Promise<Holter> {
  const { data } = await api.post<Holter>('/devices', input)
  return data
}

export async function updateHolter(id: string, input: UpdateHolterInput): Promise<Holter> {
  const { data } = await api.patch<Holter>(`/devices/${id}`, input)
  return data
}

export async function deleteHolter(id: string): Promise<Holter> {
  const { data } = await api.delete<Holter>(`/devices/${id}`)
  return data
}

export async function reassignHolter(input: ReassignHolterInput): Promise<Holter> {
  const { data } = await api.post<Holter>(`/devices/${input.holterId}/reassign`, {
    patientId: input.newPatientId,
  })
  return data
}

export async function getHolterHealth(id: string): Promise<HolterHealth> {
  const { data } = await api.get<HolterHealth>(`/devices/${id}/health`)
  return data
}

export async function assignHolter(input: AssignHolterInput): Promise<Holter> {
  const { data } = await api.post<Holter>(`/devices/${input.holterId}/assign`, {
    patientId: input.patientId,
  })
  return data
}

export async function unassignHolter(holterId: string): Promise<Holter> {
  const { data } = await api.post<Holter>(`/devices/${holterId}/unassign`)
  return data
}
