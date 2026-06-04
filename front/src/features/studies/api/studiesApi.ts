import { api } from '@/lib/api'

import type { Study } from '../types'

export async function getStudy(id: string): Promise<Study> {
  const { data } = await api.get<Study>(`/studies/${id}`)
  return data
}
