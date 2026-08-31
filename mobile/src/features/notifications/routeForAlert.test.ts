import { describe, expect, it } from 'vitest'

import type { PatientAlert } from '@/features/patient/types'
import { routeForAlert } from './routeForAlert'

const base: PatientAlert = {
  id: 'alert-1',
  kind: 'tachycardia',
  severity: 'high',
  message: 'Aviso',
  detectedAt: '2026-08-31T12:00:00Z',
  requiresResponse: true,
  needsReport: true,
  reportId: null,
  answeredAt: null,
}

describe('routeForAlert', () => {
  it('abre el formulario para un aviso pendiente', () => {
    expect(routeForAlert(base)).toEqual({
      pathname: '/report',
      params: { alertId: 'alert-1', occurredAt: '2026-08-31T12:00:00Z' },
    })
  })

  it('abre la respuesta existente en modo lectura', () => {
    expect(
      routeForAlert({ ...base, needsReport: false, reportId: 'report-1', answeredAt: base.detectedAt }),
    ).toEqual({ pathname: '/report-response', params: { reportId: 'report-1' } })
  })

  it('manda el aviso del chaleco a Dispositivo', () => {
    expect(
      routeForAlert({
        ...base,
        kind: 'vest_misplaced',
        requiresResponse: false,
        needsReport: false,
      }),
    ).toEqual({ pathname: '/(tabs)/device' })
  })
})
