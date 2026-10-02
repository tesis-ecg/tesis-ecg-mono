export type HolterStatus = 'available' | 'assigned' | 'maintenance' | 'retired'

export type HolterSignalQuality = 'good' | 'fair' | 'poor' | 'none'

export interface Holter {
  id: string
  serial: string
  model: string
  firmwareVersion: string | null
  status: HolterStatus
  assignedPatientId: string | null
  /** Nombre del paciente asignado. Es lo que se muestra: el id no le dice nada a nadie. */
  assignedPatientName: string | null
  /**
   * Estudio `in_progress` que este equipo está grabando ahora, si lo hay.
   * Permite saltar del inventario al registro en curso sin pasar por el paciente.
   */
  activeStudyId: string | null
  /**
   * Médico al que el admin asignó el dispositivo (ownership device→médico).
   * El médico solo ve/gestiona los dispositivos donde figura como dueño.
   * Poblado por el backend; ausente para vistas no-admin que no lo necesitan.
   */
  assignedDoctorId?: string | null
  assignedDoctorName?: string | null
  lastSeenAt: string | null
  createdAt: string
}

/** Opción de médico para el Select de "Asignar a médico" (solo admin). */
export interface DoctorOption {
  id: string
  fullName: string
}

export interface AssignHolterToDoctorInput {
  holterId: string
  doctorId: string
}

/**
 * La API key en claro de un equipo, tal como la devuelve el backend.
 *
 * Es la credencial que el firmware del chaleco usa para subir la señal de sus
 * estudios. Se guarda cifrada además de hasheada, así que —a diferencia de la
 * contraseña de un paciente— se puede volver a leer. Solo la ve un admin.
 */
export interface HolterApiKey {
  deviceId: string
  serial: string
  apiKey: string
  /** Cuándo se generó la key que se está devolviendo. */
  rotatedAt: string
}

/** El alta devuelve el equipo **y** su API key recién generada. */
export type CreatedHolter = Holter & { apiKey: string }

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
  firmwareVersion: string | null
  telemetryAvailable: boolean
  batteryPercent: number | null
  signalDbm: number | null
  signalQuality: HolterSignalQuality | null
  lastPingAt: string
  nextScheduledUploadAt: string | null
  uploadsToday: number | null
  storageUsedMb: number | null
  storageTotalMb: number | null
}
