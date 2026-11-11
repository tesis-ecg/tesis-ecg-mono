/**
 * Matemática de las herramientas de medición sobre el trazado.
 *
 * Todo se calcula con los valores y las horas de las muestras, nunca con
 * píxeles: una medida tomada sobre la pantalla no es una medida sobre la señal
 * (el zoom, el resumen min/max y el alto del gráfico la deforman). Los puntos que
 * pone el usuario viven en epoch ms, anclados a la señal, y se resuelven contra
 * la muestra más cercana.
 *
 * Un hueco entre tramos de grabación nunca se mide a través: esos instantes no
 * fueron contiguos, y una duración, una amplitud o un área entre ellos no
 * existen.
 */

export type MeasurementTool = 'time' | 'amplitude' | 'area' | 'rr'
export type ChartTool = MeasurementTool | 'finding'

export interface FindingRange {
  startMs: number
  endMs: number
}

/** Normaliza contra las muestras disponibles sin permitir cortes de grabación. */
export function findingRange(trace: MeasureTrace, aMs: number, bMs: number): FindingRange | null {
  const pair = resolvePair(trace, aMs, bMs)
  if (!pair) return null
  const startMs = trace.xs[Math.min(pair.ia, pair.ib)]
  const endMs = trace.xs[Math.max(pair.ia, pair.ib)]
  return endMs > startMs ? { startMs, endMs } : null
}

/**
 * Traza tal como está dibujada. `xs` en epoch ms ascendente, `ys` en mV (con
 * `null`/NaN donde el visor corta la línea). `gapIndices` son los índices donde
 * arranca un tramo nuevo, igual que `ECGSignal.gapIndices`.
 */
export interface MeasureTrace {
  xs: ArrayLike<number>
  ys: ArrayLike<number | null>
  gapIndices: number[]
}

/** Los dos puntos que puso el usuario, en epoch ms (se mueven con pan y zoom). */
export interface Measurement {
  id: string
  tool: MeasurementTool
  aMs: number
  bMs: number
  aMv?: number
  bMv?: number
}

const DEFAULT_MAX_MARKS = 200

function isFiniteSample(trace: MeasureTrace, i: number): boolean {
  const y = trace.ys[i]
  return y != null && Number.isFinite(y) && Number.isFinite(trace.xs[i])
}

/** Índice de la muestra más cercana en el tiempo; -1 si la traza está vacía. */
export function nearestSampleIndex(trace: MeasureTrace, epochMs: number): number {
  const n = trace.xs.length
  if (n === 0 || !Number.isFinite(epochMs)) return -1
  let lo = 0
  let hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (trace.xs[mid] < epochMs) lo = mid + 1
    else hi = mid
  }
  // `lo` es la primera muestra >= epochMs; la anterior puede estar más cerca.
  if (lo > 0 && epochMs - trace.xs[lo - 1] <= trace.xs[lo] - epochMs) return lo - 1
  return lo
}

/** Muestra finita más cercana (salta NaN/null); -1 si no hay ninguna. */
function nearestFiniteIndex(trace: MeasureTrace, epochMs: number): number {
  const start = nearestSampleIndex(trace, epochMs)
  if (start < 0) return -1
  if (isFiniteSample(trace, start)) return start
  const n = trace.xs.length
  for (let d = 1; d < n; d++) {
    const left = start - d
    const right = start + d
    const leftOk = left >= 0 && isFiniteSample(trace, left)
    const rightOk = right < n && isFiniteSample(trace, right)
    if (leftOk && rightOk) {
      return epochMs - trace.xs[left] <= trace.xs[right] - epochMs ? left : right
    }
    if (leftOk) return left
    if (rightOk) return right
    if (left < 0 && right >= n) break
  }
  return -1
}

/** ¿Hay un corte de grabación entre las muestras más cercanas a `aMs` y `bMs`? */
export function spansGap(trace: MeasureTrace, aMs: number, bMs: number): boolean {
  if (trace.gapIndices.length === 0) return false
  const ia = nearestSampleIndex(trace, aMs)
  const ib = nearestSampleIndex(trace, bMs)
  if (ia < 0 || ib < 0) return false
  return gapBetween(trace, Math.min(ia, ib), Math.max(ia, ib))
}

/** Un tramo arranca en `g` con `lo < g <= hi`: las dos muestras no son del mismo tramo. */
function gapBetween(trace: MeasureTrace, lo: number, hi: number): boolean {
  for (const g of trace.gapIndices) {
    if (g > lo && g <= hi) return true
  }
  return false
}

/** Duración absoluta y frecuencia equivalente (60000 / duración). */
export function measureTime(
  aMs: number,
  bMs: number,
): { durationMs: number; rateBpm: number | null } {
  const durationMs = Math.abs(bMs - aMs)
  if (!Number.isFinite(durationMs)) return { durationMs: 0, rateBpm: null }
  return { durationMs, rateBpm: durationMs > 0 ? 60000 / durationMs : null }
}

/** Resuelve las dos muestras finitas más cercanas, o null si cruzan un hueco. */
function resolvePair(
  trace: MeasureTrace,
  aMs: number,
  bMs: number,
): { ia: number; ib: number } | null {
  const ia = nearestFiniteIndex(trace, aMs)
  const ib = nearestFiniteIndex(trace, bMs)
  if (ia < 0 || ib < 0) return null
  if (gapBetween(trace, Math.min(ia, ib), Math.max(ia, ib))) return null
  return { ia, ib }
}

/**
 * Amplitud en cada punto y diferencia (b - a), leídas de las muestras más
 * cercanas. null si no hay muestras finitas o si los puntos cruzan un hueco.
 */
export function measureAmplitude(
  trace: MeasureTrace,
  aMs: number,
  bMs: number,
): { aMv: number; bMv: number; deltaMv: number; durationMs: number } | null {
  const pair = resolvePair(trace, aMs, bMs)
  if (!pair) return null
  const aMv = trace.ys[pair.ia] as number
  const bMv = trace.ys[pair.ib] as number
  return {
    aMv,
    bMv,
    deltaMv: bMv - aMv,
    durationMs: Math.abs(trace.xs[pair.ib] - trace.xs[pair.ia]),
  }
}

/**
 * Área entre la traza y la cuerda recta que une las muestras de los extremos
 * (integral trapezoidal de y - cuerda). Positiva si la señal queda por encima.
 * mV·ms equivale a µV·s. null si cruza un hueco o hay menos de 2 muestras.
 */
export function measureArea(
  trace: MeasureTrace,
  aMs: number,
  bMs: number,
): { areaMvMs: number; durationMs: number } | null {
  const pair = resolvePair(trace, aMs, bMs)
  if (!pair) return null
  const first = Math.min(pair.ia, pair.ib)
  const last = Math.max(pair.ia, pair.ib)
  if (first === last) return null

  const t0 = trace.xs[first]
  const t1 = trace.xs[last]
  const y0 = trace.ys[first] as number
  const y1 = trace.ys[last] as number
  const durationMs = t1 - t0
  if (!(durationMs > 0)) return null

  const slope = (y1 - y0) / durationMs
  let area = 0
  let prevT = 0
  let prevD = 0
  let havePrev = false
  let count = 0
  for (let i = first; i <= last; i++) {
    if (!isFiniteSample(trace, i)) continue // un NaN interior se salta: se une entre vecinas finitas
    const t = trace.xs[i]
    const d = (trace.ys[i] as number) - (y0 + slope * (t - t0))
    if (havePrev) area += ((prevD + d) / 2) * (t - prevT)
    prevT = t
    prevD = d
    havePrev = true
    count++
  }
  if (count < 2) return null
  return { areaMvMs: area, durationMs }
}

/**
 * Marcas del compás R-R: el intervalo entre a y b se repite hacia los dos lados
 * (aMs + k·intervalo, k puede ser negativo) dentro del rango visible. Si hay más
 * de `maxMarks`, se conservan las más cercanas a `aMs`.
 */
export function rrCompassMarks(
  aMs: number,
  bMs: number,
  rangeStartMs: number,
  rangeEndMs: number,
  maxMarks: number = DEFAULT_MAX_MARKS,
): { intervalMs: number; rateBpm: number | null; marksMs: number[] } {
  const { durationMs: intervalMs, rateBpm } = measureTime(aMs, bMs)
  const empty = { intervalMs, rateBpm, marksMs: [] as number[] }
  if (
    !(intervalMs > 0) ||
    !Number.isFinite(aMs) ||
    !Number.isFinite(rangeStartMs) ||
    !Number.isFinite(rangeEndMs) ||
    rangeEndMs < rangeStartMs ||
    maxMarks < 1
  ) {
    return empty
  }

  const kMin = Math.ceil((rangeStartMs - aMs) / intervalMs)
  const kMax = Math.floor((rangeEndMs - aMs) / intervalMs)
  if (kMax < kMin) return empty

  let kStart = kMin
  let kEnd = kMax
  const total = kMax - kMin + 1
  if (total > maxMarks) {
    const center = Math.min(Math.max(0, kMin), kMax)
    kStart = Math.max(kMin, Math.min(center - Math.floor(maxMarks / 2), kMax - maxMarks + 1))
    kEnd = kStart + maxMarks - 1
  }

  const marksMs: number[] = []
  for (let k = kStart; k <= kEnd; k++) marksMs.push(aMs + k * intervalMs)
  return { intervalMs, rateBpm, marksMs }
}

const NUMBER_FORMATS = new Map<number, Intl.NumberFormat>()

function fmt(value: number, decimals: number): string {
  let nf = NUMBER_FORMATS.get(decimals)
  if (!nf) {
    nf = new Intl.NumberFormat('es-AR', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })
    NUMBER_FORMATS.set(decimals, nf)
  }
  // Evita "-0,00" cuando el valor redondea a cero.
  const text = nf.format(value)
  return /^-[0,.]+$/.test(text) ? text.slice(1) : text
}

const NOT_MEASURABLE = '—'

/** Etiqueta en es-AR de una medición ("412 ms · 146 lpm"); "—" si no se puede medir. */
export function formatMeasurement(m: Measurement, trace: MeasureTrace | null): string {
  switch (m.tool) {
    case 'time':
    case 'rr': {
      if (trace && spansGap(trace, m.aMs, m.bMs)) return NOT_MEASURABLE
      const { durationMs, rateBpm } = measureTime(m.aMs, m.bMs)
      if (!(durationMs > 0)) return NOT_MEASURABLE
      const base = `${fmt(durationMs, 0)} ms`
      const rate = rateBpm != null ? ` · ${fmt(rateBpm, 0)} lpm` : ''
      return `${m.tool === 'rr' ? 'R-R ' : ''}${base}${rate}`
    }
    case 'amplitude': {
      if (!trace) return NOT_MEASURABLE
      const r = measureAmplitude(trace, m.aMs, m.bMs)
      return r ? `Δ ${fmt(r.deltaMv, 2)} mV` : NOT_MEASURABLE
    }
    case 'area': {
      if (!trace) return NOT_MEASURABLE
      const r = measureArea(trace, m.aMs, m.bMs)
      return r ? `Área ${fmt(r.areaMvMs, 1)} µV·s` : NOT_MEASURABLE
    }
  }
}
