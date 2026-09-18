/**
 * La retícula del papel de ECG, dibujada a escala real.
 *
 * Reemplaza a la grilla genérica de uPlot, que pone sus divisiones donde le
 * queden números redondos. En un ECG la grilla **es** el instrumento de medida:
 * el cuadro chico son 40 ms × 0,1 mV y el grande 200 ms × 0,5 mV, y es sobre
 * esos cuadros que se lee el ancho del QRS o un desnivel del ST. Una grilla en
 * otra posición no es un detalle estético, es una regla mal graduada.
 *
 * Se cuelga del hook `drawClear` igual que `annotationPlugin`, o sea antes de
 * que uPlot pinte la serie: la retícula tiene que quedar **debajo** del trazado.
 */

import uPlotRuntime from 'uplot'
import type uPlot from 'uplot'

import type { PaperScale } from './paperScale'
import { LARGE_BOX_MM, SMALL_BOX_MM } from './paperScale'

export interface PaperGridColors {
  minor: string
  major: string
}

/**
 * Por debajo de esto no se dibuja la retícula fina.
 *
 * Con el zoom libre muy alejado los renglones de 1 mm caen a menos de un píxel
 * de distancia: el resultado no es una grilla sino un fondo gris uniforme que
 * tapa el trazado y no se puede contar. Cuando no se puede contar, no sirve como
 * regla y es mejor no dibujarla.
 */
const MIN_MINOR_SPACING_PX = 3

function drawLines(
  ctx: CanvasRenderingContext2D,
  positions: number[],
  vertical: boolean,
  from: number,
  to: number,
  color: string,
  width: number,
) {
  if (positions.length === 0) return
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.beginPath()
  for (const position of positions) {
    // El medio píxel evita que una línea de 1 px se reparta entre dos columnas
    // de píxeles y salga difusa, que en una retícula de 1 mm se nota mucho.
    const aligned = Math.round(position) + 0.5
    if (vertical) {
      ctx.moveTo(aligned, from)
      ctx.lineTo(aligned, to)
    } else {
      ctx.moveTo(from, aligned)
      ctx.lineTo(to, aligned)
    }
  }
  ctx.stroke()
}

/**
 * Posiciones en px de los renglones separados `stepValue` unidades de escala.
 *
 * Se ancla en un múltiplo exacto de `stepValue` y no en el borde del viewport:
 * así los renglones no se deslizan al hacer pan, que es lo que delataría que la
 * grilla se está dibujando desde la ventana en vez de desde la escala.
 */
function gridPositions(
  u: uPlot,
  axis: 'x' | 'y',
  stepValue: number,
  min: number,
  max: number,
): number[] {
  if (stepValue <= 0) return []
  const positions: number[] = []
  const first = Math.ceil(min / stepValue) * stepValue
  // Cota de seguridad: con un zoom absurdo esto podría intentar millones de
  // líneas y colgar el hilo principal.
  const count = Math.floor((max - first) / stepValue) + 1
  if (count <= 0 || count > 20_000) return []
  for (let i = 0; i < count; i++) {
    positions.push(u.valToPos(first + i * stepValue, axis, true))
  }
  return positions
}

export function drawPaperGrid(u: uPlot, scale: PaperScale, colors: PaperGridColors) {
  const { ctx, bbox } = u
  const xScale = u.scales.x
  const yScale = u.scales.y
  if (xScale.min == null || xScale.max == null || yScale.min == null || yScale.max == null) return

  const ratio = uPlotRuntime.pxRatio || 1
  // Cuánto vale un milímetro en unidades de cada eje. Sale de la escala, no del
  // viewport: es lo que hace que un cuadro mida lo mismo con cualquier zoom.
  const secPerMm = 1 / scale.paperSpeed
  const mvPerMm = 1 / scale.amplitude

  const minorSpacingPx = scale.pxPerMm * ratio
  const drawMinor = minorSpacingPx >= MIN_MINOR_SPACING_PX

  const left = bbox.left
  const right = bbox.left + bbox.width
  const top = bbox.top
  const bottom = bbox.top + bbox.height

  ctx.save()
  ctx.beginPath()
  ctx.rect(left, top, bbox.width, bbox.height)
  ctx.clip()

  if (drawMinor) {
    drawLines(
      ctx,
      gridPositions(u, 'x', secPerMm * SMALL_BOX_MM, xScale.min, xScale.max),
      true,
      top,
      bottom,
      colors.minor,
      ratio,
    )
    drawLines(
      ctx,
      gridPositions(u, 'y', mvPerMm * SMALL_BOX_MM, yScale.min, yScale.max),
      false,
      left,
      right,
      colors.minor,
      ratio,
    )
  }

  drawLines(
    ctx,
    gridPositions(u, 'x', secPerMm * LARGE_BOX_MM, xScale.min, xScale.max),
    true,
    top,
    bottom,
    colors.major,
    ratio,
  )
  drawLines(
    ctx,
    gridPositions(u, 'y', mvPerMm * LARGE_BOX_MM, yScale.min, yScale.max),
    false,
    left,
    right,
    colors.major,
    ratio,
  )

  ctx.restore()
}
