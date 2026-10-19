import { describe, expect, it, vi } from 'vitest'

import type uPlot from 'uplot'

import { drawUnverifiedTimeBands, formatUncertainty } from './unverifiedTimePlugin'
import type { ECGTimelineSegment } from './types'

describe('formatUncertainty', () => {
  it('elige la unidad que se lee', () => {
    expect(formatUncertainty(50)).toBe('±50 ms')
    expect(formatUncertainty(7_000)).toBe('±7 s')
    expect(formatUncertainty(300_000)).toBe('±5 min')
    expect(formatUncertainty(5_400_000 * 2)).toBe('±3,0 h')
  })
})

describe('drawUnverifiedTimeBands', () => {
  const segment = (ordinal: number, verified: boolean, uncertainty: number | null) =>
    ({
      ordinal,
      startSampleIndex: 0,
      sampleCount: 0,
      startEpochMs: ordinal * 10_000,
      endEpochMs: ordinal * 10_000 + 5_000,
      bootId: ordinal,
      anchorSource: 'ntp',
      anchorUncertaintyMs: uncertainty,
      anchorMatchesBoot: verified,
    }) satisfies ECGTimelineSegment

  it('pinta solo los tramos sin verificar, con su incertidumbre', () => {
    const ctx = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      fillRect: vi.fn(),
      fillText: vi.fn(),
      measureText: () => ({ width: 40 }),
      fillStyle: '',
      font: '',
      textBaseline: '',
    }
    const plot = {
      ctx,
      bbox: { left: 0, top: 0, width: 1000, height: 100 },
      valToPos: (value: number) => value * 10,
    } as unknown as uPlot

    drawUnverifiedTimeBands(plot, [segment(0, true, 10), segment(1, false, 7_000)], 0, {
      stroke: 'violet',
      fill: 'lilac',
    })

    expect(ctx.fillRect).toHaveBeenCalledTimes(1)
    expect(ctx.fillRect).toHaveBeenCalledWith(100, 0, 50, 100)
    expect(ctx.fillText).toHaveBeenCalledWith('Hora estimada ±7 s', 104, 96)
  })
})
