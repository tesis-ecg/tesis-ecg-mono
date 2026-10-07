import type { ECGSignal } from '../types'

/**
 * Índices de muestra `[from, to)` que caen en `[minSec, maxSec]` del eje X.
 *
 * No alcanza con `segundos × sampleRate`: el eje X es hora de pared, así que un
 * estudio con huecos o reinicios del equipo tiene más segundos que muestras, y
 * los puntos pueden venir submuestreados. Con la cuenta directa, al final del
 * estudio los índices caían fuera del array y el rango vertical se calculaba
 * sobre nada. Se busca por timestamp, que es lo mismo que dibuja uPlot.
 */
export function sampleRangeForSeconds(
  signal: ECGSignal,
  minSec: number,
  maxSec: number,
): [number, number] {
  const n = signal.samples.length
  const timestamps = signal.timestampsMs
  if (timestamps.length !== n || n === 0) {
    const perSec = signal.durationMs > 0 ? n / (signal.durationMs / 1000) : 0
    return [Math.max(0, Math.floor(minSec * perSec)), Math.min(n, Math.ceil(maxSec * perSec))]
  }
  const minMs = signal.startTimestamp + minSec * 1000
  const maxMs = signal.startTimestamp + maxSec * 1000
  return [lowerBound(timestamps, minMs), lowerBound(timestamps, maxMs, true)]
}

/**
 * Lo mismo sobre un eje que ya está en segundos desde el inicio, como el que
 * recibe uPlot. Es para lo que no sale de `signal.samples`: el resumen con las
 * muestras de un tramo empalmadas tiene otros puntos.
 */
export function pointRangeForSeconds(
  xs: ArrayLike<number>,
  minSec: number,
  maxSec: number,
): [number, number] {
  return [lowerBound(xs, minSec), lowerBound(xs, maxSec, true)]
}

/** Primer índice con `values[i] >= target` (o `> target` si `inclusive`). */
function lowerBound(values: ArrayLike<number>, target: number, inclusive = false): number {
  let lo = 0
  let hi = values.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (values[mid] < target || (inclusive && values[mid] === target)) lo = mid + 1
    else hi = mid
  }
  return lo
}
