export type PatientStudyStatus = 'active' | 'completed' | 'paused' | 'none'

export type PatientSex = 'M' | 'F' | 'X'

export interface Patient {
  id: string
  fullName: string
  dni: string
  age: number
  sex: PatientSex
  assignedDeviceId: string | null
  studyStatus: PatientStudyStatus
  lastDataReceivedAt: string | null
  contactEmail: string | null
  contactPhone: string | null
}

export interface PatientListParams {
  q?: string
  status?: PatientStudyStatus[]
  limit?: number
  offset?: number
  sort?: 'name' | 'lastDataReceivedAt'
  order?: 'asc' | 'desc'
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

export type DeviceSignalQuality = 'good' | 'fair' | 'poor' | 'none'

export interface PatientDevice {
  deviceId: string
  serial: string
  model: string
  firmwareVersion: string
  batteryPercent: number
  signalDbm: number
  signalQuality: DeviceSignalQuality
  lastPingAt: string
  nextScheduledUploadAt: string
  uploadsToday: number
  storageUsedMb: number
  storageTotalMb: number
}
