import uPlotRuntime from 'uplot'
import type uPlot from 'uplot'

import { isSegmentVerified } from './timeVerification'
import type { ECGTimelineSegment } from './types'

export interface UnverifiedTimeColors {
  /** Color del rótulo. */
  stroke: string
  /** Relleno de la banda. */
  fill: string
}

/** "±7 s", "±2 min" o "±1,5 h": la incertidumbre del ancla, legible. */
export function formatUncertainty(ms: number): string {
  if (ms < 1_000) return `±${Math.round(ms)} ms`
  const seconds = ms / 1_000
  if (seconds < 120) return `±${Math.round(seconds)} s`
  const minutes = seconds / 60
  if (minutes < 120) return `±${Math.round(minutes)} min`
  return `±${(minutes / 60).toFixed(1).replace('.', ',')} h`
}

/** Texto del rótulo de un tramo sin verificar. */
export function unverifiedSegmentLabel(segment: ECGTimelineSegment): string {
  return segment.anchorUncertaintyMs != null
    ? `Hora estimada ${formatUncertainty(segment.anchorUncertaintyMs)}`
    : 'Hora estimada'
}

/**
 * Banda sutil sobre cada tramo cuya hora de pared no está verificada, con el
 * rótulo "Hora estimada ±incertidumbre". Va en `drawClear`, debajo de los avisos
 * y de la traza, igual que `drawAnnotationBands`.
 */
export function drawUnverifiedTimeBands(
  u: uPlot,
  timeline: ECGTimelineSegment[],
  recordingStartMs: number,
  colors: UnverifiedTimeColors,
) {
  const unverified = timeline.filter((segment) => !isSegmentVerified(segment))
  if (unverified.length === 0) return
  const { ctx, bbox } = u
  // `pxRatio` es estático en uPlot: el bbox viene en píxeles de dispositivo.
  const ratio = uPlotRuntime.pxRatio || 1
  ctx.save()
  ctx.beginPath()
  ctx.rect(bbox.left, bbox.top, bbox.width, bbox.height)
  ctx.clip()
  ctx.font = `${11 * ratio}px sans-serif`
  // Explícito: el contexto hereda la alineación de lo que se dibujó antes y, a la
  // derecha, el rótulo quedaba cortado contra el borde izquierdo del plot.
  ctx.textAlign = 'left'
  ctx.textBaseline = 'bottom'
  for (const segment of unverified) {
    const xStart = u.valToPos((segment.startEpochMs - recordingStartMs) / 1000, 'x', true)
    const xEnd = u.valToPos((segment.endEpochMs - recordingStartMs) / 1000, 'x', true)
    const left = Math.min(xStart, xEnd)
    const width = Math.max(Math.abs(xEnd - xStart), 1)
    if (left + width < bbox.left || left > bbox.left + bbox.width) continue
    ctx.fillStyle = colors.fill
    ctx.fillRect(left, bbox.top, width, bbox.height)
    // Abajo y no arriba: arriba van los rótulos de los avisos. Se ancla al borde
    // visible del tramo, y solo si entra.
    const label = unverifiedSegmentLabel(segment)
    const labelX = Math.max(left, bbox.left) + 4 * ratio
    if (
      ctx.measureText(label).width + 8 * ratio >
      Math.min(left + width, bbox.left + bbox.width) - Math.max(left, bbox.left)
    )
      continue
    ctx.fillStyle = colors.stroke
    ctx.fillText(label, labelX, bbox.top + bbox.height - 4 * ratio)
  }
  ctx.restore()
}
