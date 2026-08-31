import { describe, expect, it, vi } from 'vitest'

import {
  annotationLabel,
  annotationMidpoint,
  compareAnnotationsBySeverity,
  focusViewerOnAnnotation,
} from './annotationMeta'
import type { ECGAnnotation } from './types'

describe('annotation selection', () => {
  it('enfoca el punto medio usando jumpTo sin cambiar el zoom', () => {
    const viewer = { jumpTo: vi.fn() }
    const selected = annotation({ startMs: 20_000, endMs: 26_000 })

    focusViewerOnAnnotation(viewer, selected)

    expect(annotationMidpoint(selected)).toBe(23_000)
    expect(viewer.jumpTo).toHaveBeenCalledWith(23_000)
  })

  it('usa el inicio como punto medio de un evento puntual o malformado', () => {
    expect(annotationMidpoint(annotation({ startMs: 5_000, endMs: 5_000 }))).toBe(5_000)
    expect(annotationMidpoint(annotation({ startMs: 5_000, endMs: 4_000 }))).toBe(5_000)
  })
})

describe('compareAnnotationsBySeverity', () => {
  it('ordena de más crítico a menos crítico y desempata cronológicamente', () => {
    const sorted = [
      annotation({ id: 'low', severity: 'low', startMs: 1_000 }),
      annotation({ id: 'critical-later', severity: 'critical', startMs: 3_000 }),
      annotation({ id: 'high', severity: 'high', startMs: 500 }),
      annotation({ id: 'critical-earlier', severity: 'critical', startMs: 2_000 }),
      annotation({ id: 'medium', severity: 'medium', startMs: 100 }),
    ].sort(compareAnnotationsBySeverity)

    expect(sorted.map((item) => item.id)).toEqual([
      'critical-earlier',
      'critical-later',
      'high',
      'medium',
      'low',
    ])
  })
})

describe('annotationLabel', () => {
  it('usa una etiqueta legible para tipos futuros desconocidos', () => {
    expect(annotationLabel('ventricular_couplet')).toBe('Ventricular couplet')
  })

  it('distingue lo que marcó el paciente desde la app de lo que marcó el chaleco', () => {
    // Son dos cosas distintas: `symptom_marker` es el botón físico del equipo,
    // `patient_report` es el formulario de la app. Si compartieran etiqueta, el
    // médico no podría saber cuál de las dos tiene síntomas y actividad
    // cargados detrás.
    expect(annotationLabel('patient_report')).toBe('Registro del paciente')
    expect(annotationLabel('symptom_marker')).toBe('Síntoma marcado por el paciente')
  })
})

function annotation(overrides: Partial<ECGAnnotation>): ECGAnnotation {
  return {
    id: 'event-1',
    kind: 'lead_off',
    category: 'signal_quality',
    severity: 'medium',
    startMs: 0,
    endMs: 0,
    confidenceScore: null,
    ...overrides,
  }
}
