import { ANNOTATION_SEVERITY, buildAnnotationLinks } from './annotationMeta'
import type { ECGAnnotation, ECGAnnotationGroup, ECGTimelineRange } from './types'

export const MAX_TIMELINE_MS = 12 * 60 * 60 * 1000

export function timelineRange(
  startMs: number,
  durationMs: number,
  requestedStartMs = startMs,
): ECGTimelineRange {
  const duration = Math.max(0, durationMs)
  const span = Math.min(duration, MAX_TIMELINE_MS)
  const start = Math.max(startMs, Math.min(startMs + duration - span, requestedStartMs))
  return { startMs: start, endMs: start + span }
}

export function followTimeline(
  range: ECGTimelineRange,
  viewport: ECGTimelineRange,
  studyStartMs: number,
  durationMs: number,
): ECGTimelineRange {
  const span = range.endMs - range.startMs
  const viewportSpan = viewport.endMs - viewport.startMs
  const center = (viewport.startMs + viewport.endMs) / 2
  const inside =
    viewportSpan <= span && viewport.startMs >= range.startMs && viewport.endMs <= range.endMs
  return inside ? range : timelineRange(studyStartMs, durationMs, center - span / 2)
}

export function groupTimelineAnnotations(
  annotations: ECGAnnotation[],
  range: ECGTimelineRange,
  widthPx: number,
): ECGAnnotationGroup[] {
  const span = range.endMs - range.startMs
  if (span <= 0 || widthPx <= 0) return []
  // Reservar lugar para el número completo incluso en estudios muy densos.
  const slotWidthPx = Math.max(64, 36 + String(annotations.length).length * 7)
  const slots = Math.max(1, Math.floor(widthPx / slotWidthPx))
  const slotMs = span / slots
  const links = buildAnnotationLinks(annotations)
  const groups = new Map<string, ECGAnnotationGroup>()
  for (const annotation of annotations) {
    if (links.answers.has(annotation.id)) continue
    if (annotation.endMs < range.startMs || annotation.startMs > range.endMs) continue
    const anchor = Math.max(range.startMs, annotation.startMs)
    const slot = Math.min(slots - 1, Math.floor((anchor - range.startMs) / slotMs))
    const key = `${annotation.kind}:${slot}`
    const group = groups.get(key)
    if (group) {
      group.annotationIds.push(annotation.id)
      if (
        ANNOTATION_SEVERITY[annotation.severity].rank > ANNOTATION_SEVERITY[group.severity].rank
      ) {
        group.severity = annotation.severity
      }
    } else {
      groups.set(key, {
        kind: annotation.kind,
        startMs: range.startMs + slot * slotMs,
        endMs: range.startMs + (slot + 1) * slotMs,
        severity: annotation.severity,
        annotationIds: [annotation.id],
      })
    }
  }
  return [...groups.values()].sort((a, b) => a.kind.localeCompare(b.kind) || a.startMs - b.startMs)
}

export function reconcileAnnotationGroup(
  group: ECGAnnotationGroup | null,
  annotations: ECGAnnotation[],
): ECGAnnotationGroup | null {
  if (!group) return null
  const ids = new Set(annotations.map((annotation) => annotation.id))
  const annotationIds = group.annotationIds.filter((id) => ids.has(id))
  if (annotationIds.length === 0) return null
  return annotationIds.length === group.annotationIds.length ? group : { ...group, annotationIds }
}

export function annotationIsInGroup(annotation: ECGAnnotation, group: ECGAnnotationGroup): boolean {
  return (
    group.annotationIds.includes(annotation.id) ||
    (annotation.linkedAnnotationId !== null &&
      group.annotationIds.includes(annotation.linkedAnnotationId))
  )
}

export function formatTimelineTime(timestampMs: number, withDate = false): string {
  return new Date(timestampMs).toLocaleString('es-AR', {
    ...(withDate ? { day: '2-digit', month: '2-digit' } : {}),
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
