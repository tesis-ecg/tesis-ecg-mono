import { api } from '@/lib/api'

import type {
  Study,
  StudyListParams,
  StudyListResponse,
  StudyPatientReportsResponse,
} from '../types'

export async function getStudy(id: string): Promise<Study> {
  const { data } = await api.get<Study>(`/studies/${id}`)
  return data
}

export async function listStudies(params: StudyListParams = {}): Promise<StudyListResponse> {
  const { data } = await api.get<StudyListResponse>('/studies', { params })
  return data
}

/**
 * Cierra el estudio. El backend fija `endedAt` y `durationMs`, y deja al
 * paciente en "Completado".
 *
 * Responde 409 `STUDY_NOT_OPEN` si ya estaba cerrado: es deliberado, no un
 * error a esconder. Cerrar dos veces suele significar que el médico está
 * mirando un estudio distinto del que cree.
 */
export async function completeStudy(id: string): Promise<Study> {
  const { data } = await api.post<Study>(`/studies/${id}/complete`)
  return data
}

/** Descarta el estudio: colocación fallida, datos de banco, error de carga. */
export async function cancelStudy(id: string): Promise<Study> {
  const { data } = await api.post<Study>(`/studies/${id}/cancel`)
  return data
}

/**
 * Bitácora del paciente para este estudio.
 *
 * Incluye los registros que todavía no se pueden pintar sobre el ECG: si el
 * médico solo viera las bandas del gráfico, un síntoma marcado hace veinte
 * minutos sería invisible hasta el próximo envío del chaleco.
 */
export async function getStudyPatientReports(id: string): Promise<StudyPatientReportsResponse> {
  const { data } = await api.get<StudyPatientReportsResponse>(`/studies/${id}/patient-reports`)
  return data
}
