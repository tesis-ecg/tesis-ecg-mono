import { describe, expect, it } from 'vitest'

import {
  annotationIsInGroup,
  followTimeline,
  groupTimelineAnnotations,
  MAX_TIMELINE_MS,
  reconcileAnnotationGroup,
  timelineRange,
} from './timeline'
import type { ECGAnnotation } from './types'

const HOUR = 3_600_000
function annotation(
  id: string,
  startMs: number,
  overrides: Partial<ECGAnnotation> = {},
): ECGAnnotation {
  return {
    id,
    kind: 'lead_off',
    category: 'signal_quality',
    severity: 'medium',
    startMs,
    endMs: startMs,
    confidenceScore: null,
    linkedAnnotationId: null,
    description: null,
    ...overrides,
  }
}

describe('ECG timeline windows', () => {
  it('limita a 12 horas, recorta en los extremos y no inventa duración', () => {
    expect(timelineRange(100, HOUR)).toEqual({ startMs: 100, endMs: 100 + HOUR })
    expect(timelineRange(100, 30 * HOUR, -100)).toEqual({
      startMs: 100,
      endMs: 100 + MAX_TIMELINE_MS,
    })
    expect(timelineRange(100, 30 * HOUR, 40 * HOUR)).toEqual({
      startMs: 100 + 18 * HOUR,
      endMs: 100 + 30 * HOUR,
    })
    expect(timelineRange(100, 0)).toEqual({ startMs: 100, endMs: 100 })
  })

  it('sigue al trazado solo cuando sale de la ventana y usa su centro si es más ancho', () => {
    const range = timelineRange(0, 48 * HOUR)
    expect(followTimeline(range, { startMs: HOUR, endMs: 2 * HOUR }, 0, 48 * HOUR)).toBe(range)
    expect(
      followTimeline(range, { startMs: 20 * HOUR, endMs: 20 * HOUR + 10_000 }, 0, 48 * HOUR),
    ).toEqual({ startMs: 14 * HOUR + 5_000, endMs: 26 * HOUR + 5_000 })
    expect(followTimeline(range, { startMs: 0, endMs: 24 * HOUR }, 0, 48 * HOUR)).toEqual({
      startMs: 6 * HOUR,
      endMs: 18 * HOUR,
    })
  })
})

describe('groupTimelineAnnotations', () => {
  const range = { startMs: 0, endMs: 1_000 }
  it('separa tipos, agrupa por casillero y conserva la mayor severidad', () => {
    const groups = groupTimelineAnnotations(
      [
        annotation('a', 10),
        annotation('b', 20, { severity: 'critical' }),
        annotation('c', 30, { kind: 'noise' }),
        annotation('d', 500),
      ],
      range,
      128,
    )
    expect(groups.map((group) => [group.kind, group.annotationIds, group.severity])).toEqual([
      ['lead_off', ['a', 'b'], 'critical'],
      ['lead_off', ['d'], 'medium'],
      ['noise', ['c'], 'medium'],
    ])
    expect(groups[0]).toMatchObject({ startMs: 0, endMs: 500 })
  })

  it('asigna los bordes una sola vez e incluye eventos que comenzaron antes', () => {
    const groups = groupTimelineAnnotations(
      [
        annotation('long', -100, { endMs: 600 }),
        annotation('boundary', 500),
        annotation('end', 1_000),
        annotation('past', -20),
        annotation('future', 1_001),
      ],
      range,
      128,
    )
    expect(groups.flatMap((group) => group.annotationIds)).toEqual(['long', 'boundary', 'end'])
  })

  it('no suma las respuestas vinculadas pero conserva los registros espontáneos', () => {
    const groups = groupTimelineAnnotations(
      [
        annotation('event', 10),
        annotation('reply', 15, { kind: 'patient_report', linkedAnnotationId: 'event' }),
        annotation('spontaneous', 20, { kind: 'patient_report' }),
      ],
      range,
      128,
    )
    expect(groups.flatMap((group) => group.annotationIds)).toEqual(['event', 'spontaneous'])
  })

  it('adapta la densidad al ancho sin perder miles de integrantes', () => {
    const annotations = Array.from({ length: 10_000 }, (_, i) => annotation(String(i), i))
    const wide = groupTimelineAnnotations(annotations, { startMs: 0, endMs: 10_000 }, 1_024)
    const narrow = groupTimelineAnnotations(annotations, { startMs: 0, endMs: 10_000 }, 128)
    expect(wide).toHaveLength(14)
    expect(narrow).toHaveLength(1)
    expect(wide.reduce((sum, group) => sum + group.annotationIds.length, 0)).toBe(10_000)
    expect(narrow.reduce((sum, group) => sum + group.annotationIds.length, 0)).toBe(10_000)
  })

  it('resuelve rangos y anchos vacíos', () => {
    expect(groupTimelineAnnotations([], range, 100)).toEqual([])
    expect(groupTimelineAnnotations([annotation('a', 0)], range, 0)).toEqual([])
    expect(groupTimelineAnnotations([annotation('a', 0)], { startMs: 0, endMs: 0 }, 100)).toEqual(
      [],
    )
  })

  it('mantiene los IDs seleccionados al refrescar y reconoce sus respuestas', () => {
    const annotations = [annotation('a', 10), annotation('b', 20)]
    const group = groupTimelineAnnotations(annotations, range, 64)[0]
    expect(reconcileAnnotationGroup(group, annotations)).toBe(group)
    expect(reconcileAnnotationGroup(group, [annotations[0]])?.annotationIds).toEqual(['a'])
    expect(reconcileAnnotationGroup(group, [])).toBeNull()
    expect(annotationIsInGroup(annotation('reply', 15, { linkedAnnotationId: 'a' }), group)).toBe(
      true,
    )
    expect(annotationIsInGroup(annotation('external', 15), group)).toBe(false)
  })
})
