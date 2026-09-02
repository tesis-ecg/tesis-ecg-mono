import { describe, expect, it } from 'vitest'

import { centerRangeAt, layoutVisibleAnnotationLabels } from './annotationLayout'
import type { ECGAnnotation } from './types'

describe('centerRangeAt', () => {
  it('centra conservando el span actual', () => {
    expect(centerRangeAt(50, 10, 30, 0, 100)).toEqual([40, 60])
  })

  it('conserva el span al recortar contra ambos extremos', () => {
    expect(centerRangeAt(2, 20, 40, 0, 100)).toEqual([0, 20])
    expect(centerRangeAt(98, 20, 40, 0, 100)).toEqual([80, 100])
  })
})

describe('layoutVisibleAnnotationLabels', () => {
  it('centra usando la intersección visible y oculta rangos fuera del viewport', () => {
    const layouts = layoutVisibleAnnotationLabels({
      annotations: [
        annotation({ id: 'spans-view', startMs: -1_000, endMs: 11_000 }),
        annotation({ id: 'partial', startMs: 8_000, endMs: 12_000 }),
        annotation({ id: 'hidden', startMs: 12_001, endMs: 13_000 }),
      ],
      viewportStartMs: 0,
      viewportEndMs: 10_000,
      plotWidthPx: 1_000,
      labelWidths: new Map([
        ['spans-view', 100],
        ['partial', 100],
      ]),
      selectedAnnotationId: null,
    })

    expect(layouts.find((item) => item.annotation.id === 'spans-view')?.centerPx).toBe(500)
    expect(layouts.find((item) => item.annotation.id === 'partial')?.centerPx).toBe(900)
    expect(layouts.some((item) => item.annotation.id === 'hidden')).toBe(false)
  })

  it('apila etiquetas que colisionan y ubica la seleccionada en la primera fila', () => {
    const layouts = layoutVisibleAnnotationLabels({
      annotations: [
        annotation({ id: 'regular', severity: 'critical', startMs: 4_900, endMs: 5_100 }),
        annotation({ id: 'selected', severity: 'low', startMs: 5_000, endMs: 5_200 }),
        annotation({ id: 'separate', severity: 'medium', startMs: 8_000, endMs: 8_100 }),
      ],
      viewportStartMs: 0,
      viewportEndMs: 10_000,
      plotWidthPx: 1_000,
      labelWidths: new Map([
        ['regular', 180],
        ['selected', 180],
        ['separate', 100],
      ]),
      selectedAnnotationId: 'selected',
    })

    expect(layouts.find((item) => item.annotation.id === 'selected')?.lane).toBe(0)
    expect(layouts.find((item) => item.annotation.id === 'regular')?.lane).toBe(1)
    expect(layouts.find((item) => item.annotation.id === 'separate')?.lane).toBe(0)
  })
})

function annotation(overrides: Partial<ECGAnnotation>): ECGAnnotation {
  return {
    id: 'event',
    kind: 'lead_off',
    category: 'signal_quality',
    severity: 'medium',
    startMs: 0,
    endMs: 1_000,
    confidenceScore: null,
    linkedAnnotationId: null,
    description: null,
    ...overrides,
  }
}
