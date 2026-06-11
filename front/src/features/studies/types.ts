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
