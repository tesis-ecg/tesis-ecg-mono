import type { ECGTimeVerification, ECGTimelineSegment } from './types'

/**
 * La hora de pared de un tramo es confiable solo si el ancla coincide con el
 * arranque del equipo. Un tramo sin verificar es normal: es cualquier arranque
 * durante el que el puente no tuvo red.
 */
export function isSegmentVerified(segment: ECGTimelineSegment): boolean {
  return segment.anchorMatchesBoot === true
}

/**
 * Resume cuánto de la línea de tiempo no tiene hora verificada, para que el eje
 * y el cartel digan cuántos tramos y cuánto tiempo, y no valgan para todo el
 * estudio. Sin tramos (estudio legacy) decide el manifest.
 */
export function summarizeTimeVerification(
  timeline: ECGTimelineSegment[],
  manifestVerified?: boolean,
): ECGTimeVerification {
  if (timeline.length === 0) {
    return {
      allVerified: manifestVerified ?? true,
      segmentCount: 0,
      unverifiedSegmentCount: 0,
      unverifiedDurationMs: 0,
      maxUncertaintyMs: null,
    }
  }
  let unverifiedSegmentCount = 0
  let unverifiedDurationMs = 0
  let maxUncertaintyMs: number | null = null
  for (const segment of timeline) {
    if (isSegmentVerified(segment)) continue
    unverifiedSegmentCount += 1
    unverifiedDurationMs += segment.endEpochMs - segment.startEpochMs
    const uncertainty = segment.anchorUncertaintyMs
    if (uncertainty != null && (maxUncertaintyMs === null || uncertainty > maxUncertaintyMs)) {
      maxUncertaintyMs = uncertainty
    }
  }
  return {
    allVerified: unverifiedSegmentCount === 0,
    segmentCount: timeline.length,
    unverifiedSegmentCount,
    unverifiedDurationMs,
    maxUncertaintyMs,
  }
}

/** El tramo que contiene el instante, o null en un hueco o sin línea de tiempo. */
export function segmentAtTimestamp(
  timeline: ECGTimelineSegment[],
  epochMs: number,
): ECGTimelineSegment | null {
  for (const segment of timeline) {
    if (epochMs >= segment.startEpochMs && epochMs <= segment.endEpochMs) return segment
  }
  return null
}

/**
 * Si la hora de un instante es confiable. Fuera de todo tramo (un hueco, o un
 * estudio legacy sin línea de tiempo) no hay a quién preguntarle: vale `fallback`.
 */
export function isTimestampVerified(
  timeline: ECGTimelineSegment[],
  epochMs: number,
  fallback: boolean,
): boolean {
  const segment = segmentAtTimestamp(timeline, epochMs)
  return segment ? isSegmentVerified(segment) : fallback
}
