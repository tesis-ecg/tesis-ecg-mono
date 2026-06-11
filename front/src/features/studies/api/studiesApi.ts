import { api } from '@/lib/api'

import type { Study, StudyListParams, StudyListResponse } from '../types'
import { mockStudiesList } from '../mocks'

export async function getStudy(id: string): Promise<Study> {
  const { data } = await api.get<Study>(`/studies/${id}`)
  return data
}

export async function listStudies(params: StudyListParams = {}): Promise<StudyListResponse> {
  // TODO(backend): GET /studies — ver TES-38.
  // Cuando el endpoint exista, reemplazar el mock por el call real:
  //   const { data } = await api.get<StudyListResponse>('/studies', { params })
  //   return data
  return mockStudiesList(params)
}
