// import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'
import { mockDelay } from '@/lib/mockDelay'

import { mockStudyFor } from '../mocks'
import type { Study } from '../types'

/**
 * GET /studies/:id — Metadata del estudio.
 *
 * BACKEND PENDIENTE — ver TES-32.
 */
export async function getStudy(id: string): Promise<Study> {
  // TODO(TES-32 backend): descomentar cuando el endpoint exista
  // const { data } = await api.get<Study>(`/studies/${id}`)
  // return data

  // MOCK ↓
  await mockDelay()
  const study = mockStudyFor(id)
  if (!study) {
    throw createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Estudio no encontrado.',
    })
  }
  return study
  // MOCK ↑
}
