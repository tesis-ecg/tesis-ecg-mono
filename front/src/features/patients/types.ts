export type PatientStudyStatus = 'active' | 'completed' | 'paused' | 'none'

export type PatientSex = 'M' | 'F' | 'X'

export interface Patient {
  id: string
  fullName: string
  dni: string
  birthDate: string
  sex: PatientSex
  assignedDeviceId: string | null
  /** Serial del Holter asignado. Es lo que se muestra en la grilla, no el id. */
  assignedDeviceSerial: string | null
  studyStatus: PatientStudyStatus
  lastDataReceivedAt: string | null
  contactEmail: string | null
  contactPhone: string | null
  /**
   * Médico dueño del paciente. Solo lo devuelve el backend en la vista global
   * del admin (todas las tablas de todos los médicos); ausente para médicos.
   */
  doctorId?: string | null
  doctorName?: string | null
  /** Si tiene cuenta en la app móvil. Decide qué acciones ofrece su ficha. */
  hasAppAccount: boolean
}

/**
 * Datos editables de un paciente. `assignedDeviceId`, `studyStatus` y
 * `lastDataReceivedAt` se setean en otros flujos, no en el ABM.
 */
export interface CreatePatientInput {
  fullName: string
  dni: string
  birthDate: string
  sex: PatientSex
  /** Obligatorio: es el usuario del paciente en la app móvil. */
  contactEmail: string
  contactPhone: string | null
  doctorId?: string
}

/**
 * Respuesta del alta. `generatedPassword` viaja **una sola vez**: Auth0 guarda
 * el hash y no hay endpoint que la pueda volver a leer, así que el diálogo la
 * muestra con botón de copiar antes de que se pierda.
 */
export interface CreatedPatient extends Patient {
  generatedPassword: string
}

export interface PatientPassword {
  password: string
}

export type UpdatePatientInput = Partial<CreatePatientInput>

export interface PatientListParams {
  q?: string
  status?: PatientStudyStatus[]
  limit?: number
  offset?: number
  sort?: 'name' | 'lastDataReceivedAt'
  order?: 'asc' | 'desc'
  hasDevice?: boolean
}

export interface PatientListResponse {
  items: Patient[]
  total: number
  limit: number
  offset: number
}

export type PatientStudySessionStatus = 'in_progress' | 'completed' | 'cancelled' | 'scheduled'

export interface PatientStudy {
  id: string
  patientId: string
  startedAt: string
  endedAt: string | null
  durationHours: number | null
  status: PatientStudySessionStatus
  deviceId: string
  samplesCount: number
  eventsCount: number
}

export interface PatientStudiesResponse {
  items: PatientStudy[]
  total: number
}

export type MetricTrend = 'up' | 'down' | 'flat'

export interface PatientSummary {
  windowHours: number
  heartRate: {
    averageBpm: number
    deltaBpm: number
    trend: MetricTrend
  } | null
  eventsDetected: {
    count: number
    delta: number
    trend: MetricTrend
  } | null
  adherencePercent: {
    value: number
    deltaPp: number
    trend: MetricTrend
  } | null
}
