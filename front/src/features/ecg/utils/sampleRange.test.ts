import { describe, expect, it } from 'vitest'

import type { ECGSignal } from '../types'
import { sampleRangeForSeconds } from './sampleRange'

function signalWithGap(): ECGSignal {
  // 4 puntos a 1 s, un hueco de 100 s, y 4 puntos más.
  const start = 1_000_000
  const offsets = [0, 1, 2, 3, 103, 104, 105, 106]
  return {
    samples: Float32Array.from([54, 54, 54, 54, 55, 55, 55, 55]),
    timestampsMs: Float64Array.from(offsets.map((s) => start + s * 1000)),
    startTimestamp: start,
    durationMs: 107_000,
    sampleRate: 500,
  } as unknown as ECGSignal
}

describe('sampleRangeForSeconds', () => {
  it('busca por hora de pared, no por segundos × sampleRate', () => {
    // Con la cuenta directa, 103 s × 500 caía fuera del array.
    expect(sampleRangeForSeconds(signalWithGap(), 103, 106)).toEqual([4, 8])
    expect(sampleRangeForSeconds(signalWithGap(), 0, 2)).toEqual([0, 3])
  })

  it('una ventana dentro del hueco no tiene muestras', () => {
    const [from, to] = sampleRangeForSeconds(signalWithGap(), 10, 50)
    expect(to - from).toBe(0)
  })
})
