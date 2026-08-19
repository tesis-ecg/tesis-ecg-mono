import type { AxiosError } from 'axios'
import { describe, expect, it } from 'vitest'

import { mapAxiosError, unwrapError } from './apiError'

describe('mapAxiosError', () => {
  it('preserva el contrato de error del backend', () => {
    const error = {
      response: {
        status: 422,
        data: {
          code: 'INVALID_PATIENT',
          message: 'Datos inválidos',
          fields: { birthDate: 'No puede ser futura' },
          requestId: 'req-123',
        },
      },
    } as AxiosError

    expect(mapAxiosError(error)).toMatchObject({
      status: 422,
      code: 'VALIDATION',
      serverCode: 'INVALID_PATIENT',
      message: 'Datos inválidos',
      fields: { birthDate: 'No puede ser futura' },
      requestId: 'req-123',
    })
  })

  it.each([
    ['ECONNABORTED', 'TIMEOUT'],
    ['ETIMEDOUT', 'TIMEOUT'],
    ['ERR_CANCELED', 'CANCELLED'],
    ['ERR_NETWORK', 'NETWORK'],
  ])('mapea %s a %s', (axiosCode, expected) => {
    expect(mapAxiosError({ code: axiosCode } as AxiosError).code).toBe(expected)
  })

  it('no expone objetos desconocidos como mensajes', () => {
    expect(unwrapError({ secret: 'value' })).toBe('Ocurrió un error inesperado.')
  })
})
