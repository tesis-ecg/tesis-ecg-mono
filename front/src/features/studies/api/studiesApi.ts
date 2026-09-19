import { api } from '@/lib/api'

import type {
  Study,
  StudyClinicalReportDraft,
  StudyClinicalReportDraftUpdate,
  StudyClinicalReportPreview,
  StudyClinicalReportVersion,
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

export async function getStudyClinicalReportDraft(id: string): Promise<StudyClinicalReportDraft> {
  const { data } = await api.get<StudyClinicalReportDraft>(`/studies/${id}/clinical-report/draft`)
  return data
}

export async function updateStudyClinicalReportDraft(
  id: string,
  update: StudyClinicalReportDraftUpdate,
): Promise<StudyClinicalReportDraft> {
  const { data } = await api.put<StudyClinicalReportDraft>(
    `/studies/${id}/clinical-report/draft`,
    update,
  )
  return data
}

export async function getStudyClinicalReportPreview(
  id: string,
): Promise<StudyClinicalReportPreview> {
  const { data } = await api.get<StudyClinicalReportPreview>(
    `/studies/${id}/clinical-report/preview`,
  )
  return data
}

export async function finalizeStudyClinicalReport(
  id: string,
  pdf: ArrayBuffer,
  draftRevision: number,
  snapshotHash: string,
): Promise<StudyClinicalReportVersion> {
  const { data } = await api.post<StudyClinicalReportVersion>(
    `/studies/${id}/clinical-report/finalize`,
    pdf,
    {
      params: { draftRevision, snapshotHash },
      headers: { 'Content-Type': 'application/pdf' },
      timeout: 60_000,
    },
  )
  return data
}

export async function listStudyClinicalReports(id: string): Promise<StudyClinicalReportVersion[]> {
  const { data } = await api.get<{ items: StudyClinicalReportVersion[] }>(
    `/studies/${id}/clinical-reports`,
  )
  return data.items
}

export async function downloadStudyClinicalReport(id: string, reportId: string): Promise<Blob> {
  const { data } = await api.get<Blob>(`/studies/${id}/clinical-reports/${reportId}/pdf`, {
    responseType: 'blob',
  })
  return data
}
