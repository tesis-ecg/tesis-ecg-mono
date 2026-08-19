import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/lib/api'
import { createApiError } from '@/lib/apiError'

import { getStudyEcg } from './ecgApi'

describe('getStudyEcg', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('no consulta el endpoint legacy cuando el estudio no tiene ECG', async () => {
    const error = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      serverCode: 'ECG_NOT_FOUND',
      message: 'ECG no disponible para este estudio.',
    })
    const getSpy = vi.spyOn(api, 'get').mockRejectedValue(error)

    await expect(getStudyEcg('study-id')).rejects.toBe(error)
    expect(getSpy).toHaveBeenCalledOnce()
    expect(getSpy).toHaveBeenCalledWith('/studies/study-id/ecg/manifest', {
      signal: undefined,
    })
  })

  it('usa el endpoint legacy si el backend no reconoce la ruta de manifest', async () => {
    const routeNotFound = createApiError({
      status: 404,
      code: 'NOT_FOUND',
      message: 'Recurso no encontrado.',
    })
    const legacyFailure = new Error('legacy request reached')
    const getSpy = vi
      .spyOn(api, 'get')
      .mockRejectedValueOnce(routeNotFound)
      .mockRejectedValueOnce(legacyFailure)

    await expect(getStudyEcg('study-id')).rejects.toBe(legacyFailure)
    expect(getSpy).toHaveBeenCalledTimes(2)
    expect(getSpy).toHaveBeenNthCalledWith(2, '/studies/study-id/ecg', {
      signal: undefined,
    })
  })
})
