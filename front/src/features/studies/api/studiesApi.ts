import { api } from '@/lib/api'

import type { Study, StudyListParams, StudyListResponse } from '../types'

export async function getStudy(id: string): Promise<Study> {
  const { data } = await api.get<Study>(`/studies/${id}`)
  return data
}

export async function listStudies(params: StudyListParams = {}): Promise<StudyListResponse> {
  const { data } = await api.get<StudyListResponse>('/studies', { params })
  return data
}
