import { describe, expect, it } from 'vitest'

import { annotationLabel } from './annotationMeta'
import { ectopyUnavailableText, metricsMethodNote } from './clinicalReportSummary'

describe('nombres de los hallazgos de clasificación y conducción', () => {
  it.each([
    ['ventricular_ectopy', 'Extrasístoles ventriculares (V)'],
    ['supraventricular_ectopy', 'Extrasístoles supraventriculares (S)'],
    ['svt_run', 'Posible taquicardia paroxística supraventricular (TPS)'],
    ['vt_run', 'Posible taquicardia paroxística ventricular (TPV)'],
    ['av_block_first_degree', 'Bloqueo AV de 1.er grado (PR largo)'],
    ['av_block_mobitz_1', 'Bloqueo AV de 2.º grado, Mobitz I'],
    ['av_block_mobitz_2', 'Bloqueo AV de 2.º grado, Mobitz II'],
    ['av_block_complete', 'Posible bloqueo AV completo'],
    ['wide_qrs_sustained', 'QRS ancho sostenido (posible bloqueo de rama)'],
    ['short_pr', 'PR corto'],
    ['normal_beats', 'Latidos normales'],
    ['v_max_minute', 'Máximo de V en 1 min'],
  ])('%s se llama "%s"', (kind, label) => {
    expect(annotationLabel(kind)).toBe(label)
  })
})

describe('ectopyUnavailableText', () => {
  it('no dice nada cuando hay cifras', () => {
    expect(ectopyUnavailableText(null)).toBeNull()
  })

  it('explica cada razón y cae en un texto genérico para una desconocida', () => {
    expect(ectopyUnavailableText('BEAT_CLASSIFICATION_PARTIAL')).toMatch(/parte del estudio/)
    expect(ectopyUnavailableText('ECTOPY_UNAVAILABLE')).toMatch(/Requiere clasificación/)
  })
})

describe('metricsMethodNote', () => {
  it('la versión 6 describe la clasificación N/S/V y las correcciones del médico', () => {
    const note = metricsMethodNote(6)
    expect(note).toMatch(/supraventriculares \(S\)/)
    expect(note).toMatch(/correcciones del médico/)
  })
})
