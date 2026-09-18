/**
 * El trazado en milímetros de verdad.
 *
 * En pantalla la escala solo puede garantizar la **proporción**: el navegador
 * trabaja en px CSS de 1/96 de pulgada nominal y cuánto mide eso en el vidrio
 * depende del monitor y del escalado del sistema. En papel —o en un PDF— sí hay
 * milímetros: si el SVG se dimensiona en `mm`, el motor de impresión los
 * convierte a la resolución real del dispositivo.
 *
 * Por eso el informe se arma con SVG en unidades físicas y no reutilizando el
 * canvas de uPlot: un canvas es una grilla de píxeles y al imprimirlo se escala
 * como una foto, así que la retícula dejaría de medir 1 mm.
 */

import type { ECGSignal } from './types'
import { LARGE_BOX_MM, SMALL_BOX_MM } from './paperScale'

/** Segundos por tira, la convención de cualquier electrocardiógrafo. */
export const STRIP_SECONDS = 10

export interface PrintableStripOptions {
  paperSpeed: number
  amplitude: number
  /** Alto útil del trazado, en mm. 40 mm son 4 cuadros grandes. */
  heightMm?: number
}

export interface PrintableStrip {
  /** Índice de la primera muestra de la tira. */
  startIndex: number
  /** Hora de pared del inicio, en ms epoch. */
  startEpochMs: number
  svg: string
}

function gridDefs(pxPerMmX: number, pxPerMmY: number, id: string): string {
  // Dos patrones superpuestos: el fino de 1 mm y el marcado de 5 mm. Los
  // `strokeWidth` van en mm porque todo el SVG está en mm.
  return `<defs>
  <pattern id="${id}-minor" width="${SMALL_BOX_MM * pxPerMmX}" height="${SMALL_BOX_MM * pxPerMmY}" patternUnits="userSpaceOnUse">
    <path d="M ${SMALL_BOX_MM * pxPerMmX} 0 L 0 0 0 ${SMALL_BOX_MM * pxPerMmY}" fill="none" stroke="#e8b4ad" stroke-width="0.12"/>
  </pattern>
  <pattern id="${id}-major" width="${LARGE_BOX_MM * pxPerMmX}" height="${LARGE_BOX_MM * pxPerMmY}" patternUnits="userSpaceOnUse">
    <rect width="${LARGE_BOX_MM * pxPerMmX}" height="${LARGE_BOX_MM * pxPerMmY}" fill="url(#${id}-minor)"/>
    <path d="M ${LARGE_BOX_MM * pxPerMmX} 0 L 0 0 0 ${LARGE_BOX_MM * pxPerMmY}" fill="none" stroke="#c9645a" stroke-width="0.25"/>
  </pattern>
</defs>`
}

/**
 * El pulso de calibración de 1 mV, al inicio de cada tira.
 *
 * No es decorativo: es la referencia que permite verificar la ganancia sobre el
 * papel ya impreso. A 10 mm/mV tiene que medir exactamente 10 mm de alto, y si
 * la impresora escaló la página se nota ahí antes que en ninguna onda.
 */
function calibrationPulse(amplitude: number, baselineMm: number, widthMm: number): string {
  const top = baselineMm - amplitude
  return `<path d="M 0 ${baselineMm} L ${widthMm * 0.3} ${baselineMm} L ${widthMm * 0.3} ${top} L ${widthMm} ${top} L ${widthMm} ${baselineMm} L ${widthMm * 1.3} ${baselineMm}" fill="none" stroke="#111" stroke-width="0.25"/>`
}

/**
 * Una tira de `STRIP_SECONDS` segundos, dimensionada en milímetros.
 *
 * `centerMv` es la línea de base del tramo: el front-end es DC-acoplado y el
 * offset de media celda de los electrodos puede correr el trazado decenas de mV
 * sin que sea una falla (`INTEGRACION.md` §3.2). Centrar en 0 dejaría a esos
 * pacientes con la tira en blanco.
 */
export function buildStripSvg(
  signal: ECGSignal,
  startIndex: number,
  centerMv: number,
  { paperSpeed, amplitude, heightMm = 40 }: PrintableStripOptions,
): string {
  const rate = signal.sampleRate || 500
  const count = Math.min(Math.round(STRIP_SECONDS * rate), signal.samples.length - startIndex)
  const widthMm = STRIP_SECONDS * paperSpeed
  const calibrationMm = 5 * paperSpeed * 0.04 // ~5 cuadros chicos de ancho
  const baselineMm = heightMm / 2
  const id = `s${startIndex}`

  // mm por segundo y mm por mV: la escala, literalmente.
  const mmPerSample = paperSpeed / rate
  const mmPerMv = amplitude

  let path = ''
  const gaps = new Set(signal.gapIndices)
  let pendingMove = true
  for (let i = 0; i < count; i++) {
    const index = startIndex + i
    const value = signal.samples[index]
    if (!Number.isFinite(value)) {
      pendingMove = true
      continue
    }
    // Un hueco no es una línea recta entre sus bordes: son dos instantes que
    // nunca fueron contiguos. Se corta el trazo, igual que en pantalla.
    if (gaps.has(index)) pendingMove = true
    const x = i * mmPerSample
    const y = baselineMm - (value - centerMv) * mmPerMv
    path += `${pendingMove ? 'M' : 'L'} ${x.toFixed(3)} ${y.toFixed(3)} `
    pendingMove = false
  }

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${(widthMm + calibrationMm).toFixed(2)}mm" height="${heightMm}mm" viewBox="0 0 ${(widthMm + calibrationMm).toFixed(2)} ${heightMm}">
${gridDefs(1, 1, id)}
<rect x="${calibrationMm}" y="0" width="${widthMm}" height="${heightMm}" fill="url(#${id}-major)"/>
<g transform="translate(0.5 0)">${calibrationPulse(amplitude, baselineMm, calibrationMm * 0.5)}</g>
<g transform="translate(${calibrationMm} 0)"><path d="${path.trim()}" fill="none" stroke="#111" stroke-width="0.22" stroke-linejoin="round"/></g>
</svg>`
}

/**
 * Corta la señal visible en tiras de 10 s.
 *
 * `maxStrips` existe porque un estudio de 15 días son 130.000 tiras: el informe
 * es de un tramo, no del registro entero. Quien lo pide está mirando algo
 * concreto en el visor, y eso es lo que se imprime.
 */
export function buildStrips(
  signal: ECGSignal,
  fromIndex: number,
  toIndex: number,
  options: PrintableStripOptions,
  centerFor: (from: number, to: number) => number,
  maxStrips = 12,
): PrintableStrip[] {
  const rate = signal.sampleRate || 500
  const perStrip = Math.round(STRIP_SECONDS * rate)
  const strips: PrintableStrip[] = []
  const start = Math.max(0, Math.min(fromIndex, signal.samples.length))
  const end = Math.max(start, Math.min(toIndex, signal.samples.length))

  for (let index = start; index < end && strips.length < maxStrips; index += perStrip) {
    const stripEnd = Math.min(index + perStrip, signal.samples.length)
    strips.push({
      startIndex: index,
      startEpochMs: signal.timestampsMs[index] ?? signal.startTimestamp,
      svg: buildStripSvg(signal, index, centerFor(index, stripEnd), options),
    })
  }
  return strips
}
