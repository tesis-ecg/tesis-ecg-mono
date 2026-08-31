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
  startedAt: string
  endedAt: string | null
  durationMs: number
  deviceSerial: string
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
  createdAt: string
  /** Offset dentro de la grabación; `null` mientras no haya señal debajo. */
  offsetMs: number | null
  visibleInChart: boolean
}

export interface StudyPatientReportsResponse {
  items: StudyPatientReport[]
  total: number
  /** Cuántos todavía no tienen señal debajo. */
  pendingSignalTotal: number
}
