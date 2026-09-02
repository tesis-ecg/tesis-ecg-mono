import { describe, expect, it, vi } from 'vitest'

import {
  annotationChartLabel,
  annotationLabel,
  annotationMidpoint,
  annotationResponses,
  answeredFinding,
  buildAnnotationLinks,
  compareAnnotationsBySeverity,
  compareAnnotationsForPainting,
  focusViewerOnAnnotation,
  isAnnotationHighlighted,
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

describe('vínculo entre una respuesta y el hallazgo que contesta', () => {
  const finding = annotation({ id: 'event-1', kind: 'tachycardia', startMs: 10_000, endMs: 20_000 })
  const response = annotation({
    id: 'report-1',
    kind: 'patient_report',
    category: 'patient_marker',
    severity: 'high',
    startMs: 15_000,
    endMs: 15_000,
    linkedAnnotationId: 'event-1',
    description: 'Palpitaciones',
  })
  const links = buildAnnotationLinks([finding, response])

  it('resuelve el vínculo en las dos direcciones', () => {
    expect(answeredFinding(response, links)?.id).toBe('event-1')
    expect(annotationResponses(finding, links).map((item) => item.id)).toEqual(['report-1'])
    expect(answeredFinding(finding, links)).toBeNull()
  })

  it('ignora un vínculo cuyo hallazgo no viajó en la señal', () => {
    // Un lote todavía no procesado deja el hallazgo afuera del manifest.
    // Etiquetar la marca como respuesta "de algo" sin poder decir de qué
    // confunde más que mostrarla como un registro suelto.
    const huerfana = buildAnnotationLinks([response])

    expect(answeredFinding(response, huerfana)).toBeNull()
    expect(annotationChartLabel(response, huerfana)).toBe('Registro del paciente')
  })

  it('nombra en el gráfico el hallazgo que la respuesta contesta', () => {
    expect(annotationChartLabel(response, links)).toBe('Respuesta: Taquicardia')
    expect(annotationChartLabel(finding, links)).toBe('Taquicardia')
  })

  it('resalta las dos marcas cuando se selecciona cualquiera de las dos', () => {
    expect(isAnnotationHighlighted(finding, 'report-1', links)).toBe(true)
    expect(isAnnotationHighlighted(response, 'event-1', links)).toBe(true)
    expect(isAnnotationHighlighted(finding, null, links)).toBe(false)
  })

  it('pinta la marca puntual después de la banda con la que se superpone', () => {
    // La respuesta cae dentro del hallazgo: si se pintara antes, el relleno de
    // la banda la taparía y no habría nada que ver en la traza.
    const orden = [response, finding].sort(compareAnnotationsForPainting)

    expect(orden.map((item) => item.id)).toEqual(['event-1', 'report-1'])
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
    linkedAnnotationId: null,
    description: null,
    ...overrides,
  }
}
