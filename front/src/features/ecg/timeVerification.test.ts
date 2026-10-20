import { describe, expect, it } from 'vitest'

import {
  isSegmentVerified,
  isTimestampVerified,
  segmentAtTimestamp,
  summarizeTimeVerification,
} from './timeVerification'
import type { ECGTimelineSegment } from './types'

function segment(
  ordinal: number,
  startEpochMs: number,
  endEpochMs: number,
  anchorMatchesBoot: boolean | null | undefined,
  anchorUncertaintyMs: number | null = 10,
): ECGTimelineSegment {
  return {
    ordinal,
    startSampleIndex: 0,
    sampleCount: 0,
    startEpochMs,
    endEpochMs,
    bootId: ordinal,
    anchorSource: 'ntp',
    anchorUncertaintyMs,
    anchorMatchesBoot,
  }
}

const mixed = [
  segment(0, 0, 1_000, true),
  segment(1, 2_000, 5_000, false, 7_000),
  segment(2, 6_000, 7_000, null, 500),
]

describe('isSegmentVerified', () => {
  it('solo cuenta anchorMatchesBoot === true', () => {
    expect(isSegmentVerified(segment(0, 0, 1, true))).toBe(true)
    expect(isSegmentVerified(segment(0, 0, 1, false))).toBe(false)
    expect(isSegmentVerified(segment(0, 0, 1, null))).toBe(false)
    expect(isSegmentVerified(segment(0, 0, 1, undefined))).toBe(false)
  })
})

describe('summarizeTimeVerification', () => {
  it('cuenta tramos y tiempo sin verificar, con la mayor incertidumbre', () => {
    expect(summarizeTimeVerification(mixed)).toEqual({
      allVerified: false,
      segmentCount: 3,
      unverifiedSegmentCount: 2,
      unverifiedDurationMs: 4_000,
      maxUncertaintyMs: 7_000,
    })
  })

  it('con todos los tramos verificados no hay nada que marcar', () => {
    expect(summarizeTimeVerification([segment(0, 0, 1_000, true)])).toEqual({
      allVerified: true,
      segmentCount: 1,
      unverifiedSegmentCount: 0,
      unverifiedDurationMs: 0,
      maxUncertaintyMs: null,
    })
  })

  it('la incertidumbre es null si ningún tramo sin verificar la conoce', () => {
    const summary = summarizeTimeVerification([segment(0, 0, 1_000, false, null)])
    expect(summary.maxUncertaintyMs).toBeNull()
    expect(summary.unverifiedSegmentCount).toBe(1)
  })

  it('un estudio legacy sin tramos hereda lo que dice el manifest', () => {
    expect(summarizeTimeVerification([]).allVerified).toBe(true)
    expect(summarizeTimeVerification([], true).allVerified).toBe(true)
    expect(summarizeTimeVerification([], false)).toEqual({
      allVerified: false,
      segmentCount: 0,
      unverifiedSegmentCount: 0,
      unverifiedDurationMs: 0,
      maxUncertaintyMs: null,
    })
  })
})

describe('segmentAtTimestamp / isTimestampVerified', () => {
  it('encuentra el tramo que contiene el instante, bordes incluidos', () => {
    expect(segmentAtTimestamp(mixed, 500)?.ordinal).toBe(0)
    expect(segmentAtTimestamp(mixed, 2_000)?.ordinal).toBe(1)
    expect(segmentAtTimestamp(mixed, 7_000)?.ordinal).toBe(2)
  })

  it('devuelve null en un hueco o sin línea de tiempo', () => {
    expect(segmentAtTimestamp(mixed, 1_500)).toBeNull()
    expect(segmentAtTimestamp([], 500)).toBeNull()
  })

  it('usa el tramo si lo hay y el fallback si no', () => {
    expect(isTimestampVerified(mixed, 500, false)).toBe(true)
    expect(isTimestampVerified(mixed, 3_000, true)).toBe(false)
    expect(isTimestampVerified(mixed, 1_500, true)).toBe(true)
    expect(isTimestampVerified(mixed, 1_500, false)).toBe(false)
    expect(isTimestampVerified([], 500, false)).toBe(false)
  })
})
