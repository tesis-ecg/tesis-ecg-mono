import uPlotRuntime from 'uplot'
import type uPlot from 'uplot'

import {
  EMPTY_ANNOTATION_LINKS,
  isAnnotationHighlighted,
  type ECGAnnotationLinks,
} from './annotationMeta'
import type { ECGAnnotation, ECGAnnotationSeverity } from './types'

export type ECGAnnotationColors = Record<ECGAnnotationSeverity, { stroke: string; fill: string }>

export function drawAnnotationBands(
  u: uPlot,
  annotations: ECGAnnotation[],
  recordingStartMs: number,
  colors: ECGAnnotationColors,
  selectedAnnotationId: string | null,
  links: ECGAnnotationLinks = EMPTY_ANNOTATION_LINKS,
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
  // `pxRatio` es estático en uPlot: las coordenadas del bbox vienen en
  // píxeles de dispositivo, así que los tamaños fijos del marcador tienen
  // que escalarse o en una pantalla Retina saldrían a la mitad.
  const ratio = uPlotRuntime.pxRatio || 1
  for (const annotation of drawOrder) {
    const startSec = (annotation.startMs - recordingStartMs) / 1000
    const endSec = (annotation.endMs - recordingStartMs) / 1000
    const xStart = u.valToPos(startSec, 'x', true)
    const xEnd = u.valToPos(Math.max(endSec, startSec), 'x', true)
    const color = colors[annotation.severity]
    // Resaltado y no "seleccionado": tocar una respuesta también destaca el
    // hallazgo que contesta, que es lo que hace visible la pertenencia.
    const isSelected = isAnnotationHighlighted(annotation, selectedAnnotationId, links)

    // Un instante, no un intervalo: los registros que carga el paciente desde
    // la app marcan un momento puntual. Como banda serían 2 px de relleno casi
    // invisibles entre 24 h de traza, así que se dibujan como línea con
    // banderín — que además los distingue de un hallazgo con duración.
    if (annotation.endMs <= annotation.startMs) {
      drawInstantMarker(ctx, bbox, xStart, color.stroke, isSelected, ratio)
      continue
    }

    const width = Math.max(Math.abs(xEnd - xStart), 2)
    const left = Math.min(xStart, xEnd)
    ctx.fillStyle = color.fill
    ctx.fillRect(left, bbox.top, width, bbox.height)
    ctx.strokeStyle = color.stroke
    ctx.lineWidth = isSelected ? 3 : 1
    ctx.strokeRect(left, bbox.top, width, bbox.height)
  }
  ctx.restore()
}

const MARKER_HALF_WIDTH = 5
const MARKER_HEIGHT = 9

function drawInstantMarker(
  ctx: CanvasRenderingContext2D,
  bbox: uPlot['bbox'],
  x: number,
  stroke: string,
  isSelected: boolean,
  ratio: number,
) {
  ctx.strokeStyle = stroke
  ctx.fillStyle = stroke
  ctx.lineWidth = (isSelected ? 2.5 : 1.5) * ratio

  ctx.beginPath()
  ctx.moveTo(x, bbox.top)
  ctx.lineTo(x, bbox.top + bbox.height)
  ctx.stroke()

  // Banderín triangular arriba: es lo que se ve cuando la traza está muy
  // comprimida y la línea se pierde entre los QRS.
  const half = MARKER_HALF_WIDTH * ratio * (isSelected ? 1.4 : 1)
  const height = MARKER_HEIGHT * ratio * (isSelected ? 1.4 : 1)
  ctx.beginPath()
  ctx.moveTo(x - half, bbox.top)
  ctx.lineTo(x + half, bbox.top)
  ctx.lineTo(x, bbox.top + height)
  ctx.closePath()
  ctx.fill()
}
