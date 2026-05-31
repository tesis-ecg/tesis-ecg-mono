export type HolterStatus = 'available' | 'assigned' | 'maintenance' | 'retired'

export type HolterSignalQuality = 'good' | 'fair' | 'poor' | 'none'

export interface Holter {
  id: string
  serial: string
  model: string
  firmwareVersion: string | null
  status: HolterStatus
  assignedPatientId: string | null
  lastSeenAt: string | null
  createdAt: string
}

export interface CreateHolterInput {
  serial: string
  model: string
  firmwareVersion: string | null
}

export interface UpdateHolterInput {
  model?: string
  firmwareVersion?: string | null
  status?: HolterStatus
}

export interface HolterListParams {
  q?: string
  status?: HolterStatus[]
  limit?: number
  offset?: number
}

export interface HolterListResponse {
  items: Holter[]
  total: number
  limit: number
  offset: number
}

export interface AssignHolterInput {
  holterId: string
  patientId: string
}

export interface ReassignHolterInput {
  holterId: string
  newPatientId: string
}

/**
 * Estado de salud del Holter. Vive en devices porque es propiedad del Holter,
 * no del paciente — un Holter no asignado puede tener health data si está
 * encendido y reportando. Antes se llamaba `PatientDevice` y vivía en
 * `features/patients/types.ts`.
 */
export interface HolterHealth {
  deviceId: string
  serial: string
  model: string
  firmwareVersion: string
  batteryPercent: number
  signalDbm: number
  signalQuality: HolterSignalQuality
  lastPingAt: string
  nextScheduledUploadAt: string
  uploadsToday: number
  storageUsedMb: number
  storageTotalMb: number
}
