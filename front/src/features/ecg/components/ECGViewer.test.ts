import { describe, expect, it, vi } from 'vitest'

import type uPlot from 'uplot'

import { drawAnnotationBands } from '../annotationPlugin'
import type { ECGAnnotation } from '../types'

describe('drawAnnotationBands', () => {
  it('pinta el rango y destaca el aviso seleccionado', () => {
    const context = {
      save: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      fillRect: vi.fn(),
      strokeRect: vi.fn(),
      restore: vi.fn(),
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 0,
    }
    const plot = {
      ctx: context,
      bbox: { left: 10, top: 20, width: 500, height: 200 },
      valToPos: (value: number) => value * 10,
    } as unknown as uPlot
    const annotation: ECGAnnotation = {
      id: 'selected',
      kind: 'afib',
      category: 'clinical',
      severity: 'critical',
      startMs: 2_000,
      endMs: 5_000,
      confidenceScore: 0.97,
      linkedAnnotationId: null,
      description: null,
    }
    const colors = {
      low: { stroke: 'gray', fill: 'lightgray' },
      medium: { stroke: 'blue', fill: 'lightblue' },
      high: { stroke: 'orange', fill: 'yellow' },
      critical: { stroke: 'red', fill: 'pink' },
    }

    drawAnnotationBands(plot, [annotation], 0, colors, 'selected')

    expect(context.fillRect).toHaveBeenCalledWith(20, 20, 30, 200)
    expect(context.strokeRect).toHaveBeenCalledWith(20, 20, 30, 200)
    expect(context.fillStyle).toBe('pink')
    expect(context.strokeStyle).toBe('red')
    expect(context.lineWidth).toBe(3)
  })

  it('pinta el aviso seleccionado después de los demás para mantenerlo arriba', () => {
    const context = {
      save: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      fillRect: vi.fn(),
      strokeRect: vi.fn(),
      restore: vi.fn(),
      fillStyle: '',
      strokeStyle: '',
      lineWidth: 0,
    }
    const plot = {
      ctx: context,
      bbox: { left: 0, top: 0, width: 500, height: 200 },
      valToPos: (value: number) => value * 10,
    } as unknown as uPlot
    const regular: ECGAnnotation = {
      id: 'regular',
      kind: 'noise',
      category: 'signal_quality',
      severity: 'low',
      startMs: 1_000,
      endMs: 2_000,
      confidenceScore: null,
      linkedAnnotationId: null,
      description: null,
    }
    const selected: ECGAnnotation = {
      ...regular,
      id: 'selected',
      severity: 'critical',
      startMs: 3_000,
      endMs: 4_000,
    }

    drawAnnotationBands(
      plot,
      [selected, regular],
      0,
      {
        low: { stroke: 'gray', fill: 'lightgray' },
        medium: { stroke: 'blue', fill: 'lightblue' },
        high: { stroke: 'orange', fill: 'yellow' },
        critical: { stroke: 'red', fill: 'pink' },
      },
      'selected',
    )

    expect(context.fillRect.mock.calls).toEqual([
      [10, 0, 10, 200],
      [30, 0, 10, 200],
    ])
  })
})
