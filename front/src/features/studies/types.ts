import type { PatientStudySessionStatus } from '@/features/patients/types'

/**
 * Metadata expandida de un estudio individual. Diferencia con
 * `PatientStudy` (que vive en `features/patients/types.ts`): este modelo
 * **denormaliza** `patientName` y `deviceSerial` para que la pantalla de
 * detalle no haga round-trips extra al backend, y usa `durationMs` (más
 * preciso para el formatter clínico) en vez de `durationHours`.
 *
 * Coincide con la response de `GET /studies/:id` definida en TES-32.
 */
export interface Study {
  id: string
  patientId: string
  patientName: string
  deviceId: string
  startedAt: string
  endedAt: string | null
  durationMs: number
  deviceSerial: string
  /** Si el usuario actual todavía puede consultar el estado en vivo del Holter. */
  canAccessDevice: boolean
  /** Último lote ECG recibido para este estudio, no para el paciente global. */
  lastDataReceivedAt: string | null
  status: PatientStudySessionStatus
  /**
   * Médico dueño del paciente del estudio. Solo lo devuelve el backend en la
   * vista global del admin; ausente para médicos.
   */
  doctorId?: string | null
  doctorName?: string | null
}

// Re-export para que los consumers solo importen desde `features/studies/`.
export type { PatientStudySessionStatus }

export interface StudyListParams {
  q?: string
  status?: PatientStudySessionStatus[]
  limit?: number
  offset?: number
}

export interface StudyListResponse {
  items: Study[]
  total: number
  limit: number
  offset: number
}

export type PatientReportSource = 'push_response' | 'manual'

/**
 * Un registro de la bitácora del paciente, visto desde el portal.
 *
 * `visibleInChart` es el dato que evita un malentendido clínico: un registro
 * puede existir mucho antes que la señal de ese instante, porque el chaleco
 * sube tramas una vez por hora. Mientras sea `false` no hay banda en el ECG y
 * eso no significa que se haya perdido.
 */
export interface StudyPatientReport {
  id: string
  occurredAt: string
  source: PatientReportSource
  symptoms: string[]
  /** Etiquetas ya resueltas por el backend contra el catálogo. */
  symptomLabels: string[]
  symptomsOther: string | null
  activity: string
  activityLabel: string
  activityOther: string | null
  notes: string | null
  alertId: string | null
  /**
   * Tipo del hallazgo que disparó el aviso respondido (`tachycardia`, …), ya
   * resuelto por el backend. `null` en los registros espontáneos.
   */
  alertKind: string | null
  createdAt: string
  /**
   * Offset dentro de la grabación; `null` mientras no haya señal debajo.
   *
   * Para una respuesta a un aviso **no es la hora de pared del registro** sino
   * el punto del hallazgo que contesta: es donde el visor dibuja su marca, y
   * las dos vistas tienen que coincidir para que "Ver en el ECG" lleve al
   * lugar correcto.
   */
  offsetMs: number | null
  visibleInChart: boolean
}

export interface StudyPatientReportsResponse {
  items: StudyPatientReport[]
  total: number
  /** Cuántos todavía no tienen señal debajo. */
  pendingSignalTotal: number
}

export interface StudyClinicalReportDraft {
  studyId: string
  revision: number
  indication: string | null
  medications: string | null
  referringProfessional: string | null
  technician: string | null
  clinicalObservations: string | null
  conclusion: string | null
  updatedAt: string | null
  updatedBy: string | null
  updatedByName: string | null
  updatedByRole: string | null
}

export interface StudyClinicalReportDraftUpdate {
  revision: number
  indication: string | null
  medications: string | null
  referringProfessional: string | null
  technician: string | null
  clinicalObservations: string | null
  conclusion: string | null
}

export interface StudyClinicalReportWindowPlan {
  id: string
  findingId: string
  kind: string
  category: 'clinical' | 'patient_marker'
  severity: 'low' | 'medium' | 'high' | 'critical'
  findingStartEpochMs: number
  findingEndEpochMs: number
  findingDurationMs: number
  startEpochMs: number
  endEpochMs: number
  blockIndex: number
  blockCount: number
  confidenceScore: number | null
  description: string | null
  relatedSymptoms: string[]
}

export interface StudyClinicalReportFindingSummary {
  kind: string
  count: number
  severities: string[]
  totalDurationMs: number
  longestDurationMs: number
  symptomaticCount: number
}

export interface StudyClinicalReportSnapshot {
  schemaVersion: number
  version: number
  study: {
    id: string
    status: PatientStudySessionStatus
    startedAt: string
    endedAt: string | null
    durationMs: number
    deviceSerial: string
    sampleRate: number
    isSimulated: boolean
  }
  patient: {
    id: string
    fullName: string
    dni: string
    birthDate: string | null
    sex: string
    medicalRecordNumber: string | null
  }
  responsibleDoctor: {
    fullName: string | null
    specialty: string | null
    licenseNumber: string | null
  }
  clinicalContext: StudyClinicalReportDraft
  quality: {
    recordedMs: number
    wallClockMs: number
    interruptionMs: number
    coveragePercent: number
    segments: number
    cuts: number
    lastDataReceivedAt: string | null
    synchronizationSources: string[]
    maxSynchronizationUncertaintyMs: number
  }
  findings: StudyClinicalReportFindingSummary[]
  technicalEvents: StudyClinicalReportFindingSummary[]
  patientReports: Array<{
    id: string
    occurredAt: string
    symptoms: string[]
    symptomsOther: string | null
    activity: string
    activityOther: string | null
    notes: string | null
  }>
  selectedWindows: StudyClinicalReportWindowPlan[]
}

export interface StudyClinicalReportPreview {
  draft: StudyClinicalReportDraft
  snapshot: StudyClinicalReportSnapshot
  snapshotHash: string
  windows: StudyClinicalReportWindowPlan[]
  nextVersion: number
  canGenerateDraft: boolean
  canFinalize: boolean
  blockingReasons: string[]
  issues: StudyClinicalReportIssue[]
}

export interface StudyClinicalReportIssue {
  code: string
  message: string
  severity: 'warning' | 'blocking'
}

export interface StudyClinicalReportVersion {
  id: string
  studyId: string
  version: number
  finalizedAt: string
  finalizedBy: string
  finalizedByName: string
  finalizedByRole: string
  pdfByteLength: number
  pdfSha256: string
  snapshotSha256: string
}
