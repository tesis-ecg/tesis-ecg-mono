import { describe, expect, it } from 'vitest'

import { timeVerificationMessage } from './timeVerificationBanner'
import type { ECGTimeVerification } from './types'

const base: ECGTimeVerification = {
  allVerified: false,
  segmentCount: 14,
  unverifiedSegmentCount: 2,
  unverifiedDurationMs: 72 * 60_000,
  maxUncertaintyMs: 23_500,
}

describe('timeVerificationMessage', () => {
  it('no muestra nada si todo está verificado o no hay dato', () => {
    expect(timeVerificationMessage(undefined)).toBeNull()
    expect(timeVerificationMessage({ ...base, allVerified: true })).toBeNull()
  })

  it('cuenta tramos, duración e incertidumbre', () => {
    expect(timeVerificationMessage(base)).toBe(
      '2 de 14 tramos sin hora verificada (1 h 12 min). Su hora es estimada (±23,5 s) y se marca en el eje.',
    )
  })

  it('omite la incertidumbre si no se conoce y usa el singular', () => {
    expect(
      timeVerificationMessage({
        ...base,
        segmentCount: 1,
        unverifiedSegmentCount: 1,
        unverifiedDurationMs: 30_000,
        maxUncertaintyMs: null,
      }),
    ).toBe('1 de 1 tramo sin hora verificada (30 s). Su hora es estimada y se marca en el eje.')
  })

  it('estudio legacy sin tramos conserva el mensaje global', () => {
    expect(
      timeVerificationMessage({ ...base, segmentCount: 0, unverifiedSegmentCount: 0 }),
    ).toContain('faltó un ancla')
  })
})
