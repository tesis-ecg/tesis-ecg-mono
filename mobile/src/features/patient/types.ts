/**
 * Tipos de la API `/mobile`.
 *
 * Espejo de `back/app/modules/patient_app/patient_app_schemas.py`. A diferencia
 * del portal, acá no se generan desde OpenAPI: el proyecto de Expo tiene su
 * propio bundler y sumar el paso de generación por una superficie de diez
 * endpoints costaba más de lo que resolvía. Si cambia un DTO del backend, se
 * actualiza este archivo.
 */

export type PatientSex = 'M' | 'F' | 'X'
export type PatientStudyStatus = 'active' | 'completed' | 'paused' | 'none'
export type ReportSource = 'push_response' | 'manual'
export type PushPlatform = 'ios' | 'android'

export interface Doctor {
  fullName: string
  email: string | null
}

export interface PatientProfile {
  id: string
  fullName: string
  dni: string
  birthDate: string | null
  sex: PatientSex
  email: string | null
  phone: string | null
  studyStatus: PatientStudyStatus
  doctor: Doctor | null
}

export interface Session {
  accessToken: string
  refreshToken: string
  expiresAt: string
  patient: PatientProfile
}

/**
 * Estado del chaleco.
 *
 * `state` resume en una palabra lo que la pantalla tiene que decidir:
 * - `none`: no tiene chaleco asignado
 * - `never_connected`: asignado pero todavía no se encendió
 * - `recording`: grabando y transmitiendo
 * - `idle`: conectado pero sin estudio en curso
 * - `stale`: hace horas que no manda nada
 */
export type DeviceState = 'none' | 'never_connected' | 'recording' | 'idle' | 'stale'

/**
 * Cómo tiene puesto el chaleco el paciente, según el último reporte del equipo.
 *
 * `unknown` no es `ok`: un chaleco recién entregado todavía no reportó nada, y
 * decirle al paciente que lo tiene bien puesto sin haberlo medido es
 * exactamente el error que este aviso existe para evitar.
 */
export type VestPlacement = 'ok' | 'bad' | 'unknown'

export interface DeviceStatus {
  hasDevice: boolean
  state: DeviceState
  vestPlacement: VestPlacement
  vestPlacementAt: string | null
  deviceId: string | null
  serial: string | null
  model: string | null
  firmwareVersion: string | null
  batteryPercent: number | null
  lastSeenAt: string | null
  lastDataReceivedAt: string | null
  studyId: string | null
  studyStartedAt: string | null
}

export type AlertSeverity = 'low' | 'medium' | 'high' | 'critical'

export interface PatientAlert {
  id: string
  kind: string
  severity: AlertSeverity
  message: string
  detectedAt: string
  requiresResponse: boolean
  needsReport: boolean
  reportId: string | null
  answeredAt: string | null
}

export interface AlertList {
  items: PatientAlert[]
  total: number
  pendingTotal: number
  limit: number
  offset: number
}

export interface PatientReport {
  id: string
  occurredAt: string
  source: ReportSource
  symptoms: string[]
  symptomsOther: string | null
  activity: string
  activityOther: string | null
  notes: string | null
  alertId: string | null
  studyId: string | null
  createdAt: string
}

export interface ReportList {
  items: PatientReport[]
  total: number
  limit: number
  offset: number
}

export interface ReportInput {
  occurredAt?: string
  alertId?: string
  symptoms: string[]
  symptomsOther?: string | null
  activity: string
  activityOther?: string | null
  notes?: string | null
}

export interface CatalogOption {
  value: string
  label: string
}

export interface Catalogs {
  symptoms: CatalogOption[]
  activities: CatalogOption[]
}
