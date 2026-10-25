import { describe, expect, it } from 'vitest'

import {
  isReclassifiable,
  reclassificationChoices,
  RECLASSIFICATION_KINDS,
} from './findingReclassification'
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
  it.each(['ventricular_ectopy', 'tachycardia', 'av_block_mobitz_2', 'noise_burst'])(
    'ofrece solo descartar o marcar como otro hallazgo para %s',
    (kind) => {
      expect(reclassificationChoices(finding({ kind })).map((choice) => choice.value)).toEqual([
        'reject',
        'relabel',
      ])
    },
  )
  it('incluye los hallazgos clínicos y de calidad en el selector', () => {
    const kinds = RECLASSIFICATION_KINDS.map((option) => option.value)
    expect(kinds).toContain('av_block_complete')
    expect(kinds).toContain('noise_burst')
    expect(kinds).toContain('pause')
    expect(new Set(kinds).size).toBe(kinds.length)
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
