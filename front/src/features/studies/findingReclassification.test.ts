import { describe, expect, it } from 'vitest'

import { isReclassifiable, reclassificationChoices } from './findingReclassification'
import type { StudyFinding } from './types'

function finding(overrides: Partial<StudyFinding> = {}): StudyFinding {
  return {
    id: 'f-1',
    kind: 'ventricular_ectopy',
    category: 'clinical',
    severity: 'low',
    startOffsetMs: 0,
    endOffsetMs: 1000,
    startEpochMs: 1_000,
    endEpochMs: 2_000,
    confidenceScore: null,
    modelVersion: 'ml-1',
    validationStatus: 'pending',
    reclassifiable: true,
    ...overrides,
  }
}

describe('reclassificationChoices', () => {
  it('ofrece las otras dos etiquetas de latido para una ectopia ventricular', () => {
    const values = reclassificationChoices(finding()).map((choice) => choice.value)
    expect(values).toEqual(['confirm', 'reject', 'noise', 'relabel:N', 'relabel:S'])
  })

  it('una taquicardia se puede pasar a S o V; su etiqueta propia es N', () => {
    const values = reclassificationChoices(finding({ kind: 'tachycardia' })).map((c) => c.value)
    expect(values).toEqual(['confirm', 'reject', 'noise', 'relabel:S', 'relabel:V'])
  })

  it('los tipos sin etiqueta de latido solo admiten confirmar, descartar o ruido', () => {
    const values = reclassificationChoices(finding({ kind: 'av_block_mobitz_2' })).map(
      (choice) => choice.value,
    )
    expect(values).toEqual(['confirm', 'reject', 'noise'])
  })

  it('suma restablecer solo si el hallazgo ya fue corregido', () => {
    const corrected = finding({
      validationStatus: 'noise',
      correction: {
        action: 'noise',
        beatLabel: null,
        effectiveKind: null,
        note: null,
        correctedBy: 'u-1',
        correctedByName: 'Dra. Gómez',
        correctedAt: '2026-09-17T12:00:00Z',
      },
    })
    const reset = reclassificationChoices(corrected).at(-1)!
    expect(reset).toMatchObject({ value: 'reset', request: { action: 'reset' } })
  })

  it('arma el pedido de una reclasificación con la etiqueta elegida', () => {
    const choice = reclassificationChoices(finding()).find((item) => item.value === 'relabel:S')
    expect(choice?.request).toEqual({ action: 'relabel', beatLabel: 'S' })
  })
})

describe('isReclassifiable', () => {
  it('sigue lo que decide el backend', () => {
    expect(isReclassifiable(finding())).toBe(true)
    expect(isReclassifiable(finding({ reclassifiable: false }))).toBe(false)
    expect(isReclassifiable(finding({ reclassifiable: undefined }))).toBe(false)
  })
})

describe('el aviso de descartar', () => {
  const hint = (kind: string) =>
    reclassificationChoices(finding({ kind })).find((choice) => choice.value === 'reject')?.hint

  it('depende de qué cifras mueve el tipo de hallazgo', () => {
    expect(hint('ventricular_ectopy')).toMatch(/normales \(N\)/)
    expect(hint('pause')).toMatch(/pausas/)
    expect(hint('noise_burst')).toMatch(/vuelve a contar/)
    expect(hint('av_block_mobitz_2')).toMatch(/las cifras no cambian/)
  })
})
