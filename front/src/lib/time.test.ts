import { describe, expect, it } from 'vitest'

import { formatDateTime } from './time'
import { formatWallClockShort } from '@/features/ecg/utils/formatEcgTimestamp'

describe('hora clínica de Buenos Aires', () => {
  it('muestra el mismo día y hora para un instante UTC cercano a medianoche', () => {
    const instant = '2026-10-02T02:30:00Z'
    expect(formatDateTime(instant)).toContain('01/10/2026')
    expect(formatDateTime(instant)).toContain('23:30')
    expect(formatWallClockShort(Date.parse(instant))).toBe('23:30:00')
  })
})
