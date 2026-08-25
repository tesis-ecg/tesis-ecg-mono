import { compareAnnotationsBySeverity } from './annotationMeta'
import type { ECGAnnotation } from './types'

export interface ECGAnnotationLabelLayout {
  annotation: ECGAnnotation
  centerPx: number
  lane: number
  leftPx: number
  widthPx: number
}

interface LayoutVisibleAnnotationLabelsOptions {
  annotations: ECGAnnotation[]
  viewportStartMs: number
  viewportEndMs: number
  plotWidthPx: number
  labelWidths: ReadonlyMap<string, number>
  selectedAnnotationId: string | null
  gapPx?: number
  defaultLabelWidthPx?: number
}

interface OccupiedInterval {
  leftPx: number
  rightPx: number
}

export function centerRangeAt(
  target: number,
  rangeMin: number,
  rangeMax: number,
  boundsMin: number,
  boundsMax: number,
): [number, number] {
  const span = rangeMax - rangeMin
  const totalSpan = boundsMax - boundsMin
  if (span >= totalSpan) return [boundsMin, boundsMax]

  const halfSpan = span / 2
  const nextMin = target - halfSpan
  const nextMax = target + halfSpan
  if (nextMin < boundsMin) return [boundsMin, boundsMin + span]
  if (nextMax > boundsMax) return [boundsMax - span, boundsMax]
  return [nextMin, nextMax]
}

export function layoutVisibleAnnotationLabels({
  annotations,
  viewportStartMs,
  viewportEndMs,
  plotWidthPx,
  labelWidths,
  selectedAnnotationId,
  gapPx = 6,
  defaultLabelWidthPx = 160,
}: LayoutVisibleAnnotationLabelsOptions): ECGAnnotationLabelLayout[] {
  const viewportDurationMs = viewportEndMs - viewportStartMs
  if (viewportDurationMs <= 0 || plotWidthPx <= 0) return []

  const prioritized = [...annotations].sort((a, b) => {
    const selectedOrder =
      Number(b.id === selectedAnnotationId) - Number(a.id === selectedAnnotationId)
    return selectedOrder || compareAnnotationsBySeverity(a, b)
  })
  const occupiedLanes: OccupiedInterval[][] = []
  const layouts: ECGAnnotationLabelLayout[] = []

  for (const annotation of prioritized) {
    const annotationEndMs = Math.max(annotation.endMs, annotation.startMs)
    if (annotationEndMs < viewportStartMs || annotation.startMs > viewportEndMs) continue

    const visibleStartMs = Math.max(annotation.startMs, viewportStartMs)
    const visibleEndMs = Math.min(annotationEndMs, viewportEndMs)
    const visibleCenterMs = visibleStartMs + (visibleEndMs - visibleStartMs) / 2
    const centerPx = ((visibleCenterMs - viewportStartMs) / viewportDurationMs) * plotWidthPx
    const measuredWidth = labelWidths.get(annotation.id) ?? defaultLabelWidthPx
    const widthPx = Math.min(Math.max(measuredWidth, 1), plotWidthPx)
    const leftPx = Math.max(0, Math.min(plotWidthPx - widthPx, centerPx - widthPx / 2))
    const rightPx = leftPx + widthPx
    let lane = 0

    while (
      occupiedLanes[lane]?.some(
        (interval) => leftPx < interval.rightPx + gapPx && rightPx + gapPx > interval.leftPx,
      )
    ) {
      lane += 1
    }
    occupiedLanes[lane] ??= []
    occupiedLanes[lane].push({ leftPx, rightPx })
    layouts.push({ annotation, centerPx, lane, leftPx, widthPx })
  }

  return layouts
}
