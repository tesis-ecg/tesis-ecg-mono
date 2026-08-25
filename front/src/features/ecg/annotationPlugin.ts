import type uPlot from 'uplot'

import type { ECGAnnotation, ECGAnnotationSeverity } from './types'

export type ECGAnnotationColors = Record<ECGAnnotationSeverity, { stroke: string; fill: string }>

export function drawAnnotationBands(
  u: uPlot,
  annotations: ECGAnnotation[],
  recordingStartMs: number,
  colors: ECGAnnotationColors,
  selectedAnnotationId: string | null,
) {
  if (annotations.length === 0) return
  const { ctx, bbox } = u
  const selected = annotations.find((annotation) => annotation.id === selectedAnnotationId)
  const drawOrder = selected
    ? [...annotations.filter((annotation) => annotation.id !== selectedAnnotationId), selected]
    : annotations
  ctx.save()
  ctx.beginPath()
  ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height)
  ctx.clip()
  for (const annotation of drawOrder) {
    const startSec = (annotation.startMs - recordingStartMs) / 1000
    const endSec = (annotation.endMs - recordingStartMs) / 1000
    const xStart = u.valToPos(startSec, 'x', true)
    const xEnd = u.valToPos(Math.max(endSec, startSec), 'x', true)
    const width = Math.max(Math.abs(xEnd - xStart), 2)
    const left = Math.min(xStart, xEnd)
    const color = colors[annotation.severity]
    ctx.fillStyle = color.fill
    ctx.fillRect(left, bbox.top, width, bbox.height)
    ctx.strokeStyle = color.stroke
    ctx.lineWidth = annotation.id === selectedAnnotationId ? 3 : 1
    ctx.strokeRect(left, bbox.top, width, bbox.height)
  }
  ctx.restore()
}
