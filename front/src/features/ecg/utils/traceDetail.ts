/**
 * Cuándo el visor cambia el resumen por las muestras, y cómo las empalma.
 *
 * El resumen es un nivel de la pirámide: un mínimo y un máximo por balde. Visto
 * de lejos, con un balde o menos por píxel, es exactamente lo que se dibujaría
 * con todas las muestras (uPlot también se queda con el mínimo y el máximo de
 * cada columna). De cerca deja de serlo: a 25 mm/s un balde de 128 ms ocupa
 * más de diez píxeles, un QRS entra en uno solo y la línea de base se dibuja
 * como un serrucho. Ahí hacen falta las muestras.
 */

/**
 * Hasta cuántas muestras visibles se piden las de verdad: 10 min a 500 Hz,
 * ~1,2 MB de float32. Más lejos se queda el resumen, que alcanza para navegar.
 */
export const DETAIL_MAX_VISIBLE_SAMPLES = 300_000

/** Visible más márgenes: lo más que se descarga para una vista. */
const DETAIL_MAX_WINDOW_SAMPLES = 450_000

export interface DetailRange {
  startSample: number
  endSample: number
}

/**
 * El tramo de muestras a pedir para lo visible, o `null` si el resumen alcanza.
 *
 * Pide de más a cada lado (media ventana) para que desplazarse un poco no
 * vuelva a descargar.
 */
export function detailRangeFor({
  visibleStartSample,
  visibleEndSample,
  plotWidthPx,
  overviewSamplesPerBucket,
  availableSamples,
}: {
  visibleStartSample: number
  visibleEndSample: number
  plotWidthPx: number
  overviewSamplesPerBucket: number
  availableSamples: number
}): DetailRange | null {
  const visible = visibleEndSample - visibleStartSample
  if (visible <= 0 || plotWidthPx <= 0 || availableSamples <= 0) return null
  if (visible > DETAIL_MAX_VISIBLE_SAMPLES) return null
  // Un balde por píxel o menos: el resumen ya es lo que se vería.
  if (overviewSamplesPerBucket <= visible / plotWidthPx) return null
  const margin = Math.floor(Math.min(visible / 2, (DETAIL_MAX_WINDOW_SAMPLES - visible) / 2))
  const startSample = Math.max(0, visibleStartSample - margin)
  const endSample = Math.min(availableSamples, visibleEndSample + margin)
  return endSample > startSample ? { startSample, endSample } : null
}

/** Si lo ya cargado cubre lo visible. */
export function coversRange(loaded: DetailRange | null, wanted: DetailRange): boolean {
  return (
    loaded !== null &&
    loaded.startSample <= wanted.startSample &&
    loaded.endSample >= wanted.endSample
  )
}

/**
 * El resumen con las muestras del tramo en su lugar.
 *
 * Sale del resumen todo punto que cae dentro del tramo. Las muestras van con un
 * corte (`null`) en cada borde: del otro lado hay un balde del resumen, y una
 * recta de su mínimo o su máximo a una muestra sería una línea que nadie midió.
 * Los bordes quedan en los márgenes, fuera de pantalla. Los huecos de
 * grabación dentro del tramo también cortan, como en el resumen.
 */
export function mergeDetail(
  overviewXs: ArrayLike<number>,
  overviewYs: ArrayLike<number | null>,
  detailXs: Float64Array,
  detailYs: Float32Array,
  detailGapIndices: number[],
): { xs: Float64Array; ys: (number | null)[] } {
  const n = detailXs.length
  if (n === 0) {
    return { xs: Float64Array.from(overviewXs), ys: Array.from(overviewYs) }
  }
  const before = firstIndexAtOrAfter(overviewXs, detailXs[0])
  const after = firstIndexAfter(overviewXs, detailXs[n - 1])
  const total = before + n + (overviewXs.length - after)
  const xs = new Float64Array(total)
  const ys = new Array<number | null>(total)

  for (let i = 0; i < before; i++) {
    xs[i] = overviewXs[i]
    ys[i] = overviewYs[i]
  }
  const gaps = new Set(detailGapIndices)
  for (let i = 0; i < n; i++) {
    xs[before + i] = detailXs[i]
    ys[before + i] = i === 0 || i === n - 1 || gaps.has(i) ? null : detailYs[i]
  }
  for (let i = after, out = before + n; i < overviewXs.length; i++, out++) {
    xs[out] = overviewXs[i]
    ys[out] = overviewYs[i]
  }
  return { xs, ys }
}

function firstIndexAtOrAfter(values: ArrayLike<number>, target: number): number {
  let lo = 0
  let hi = values.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (values[mid] < target) lo = mid + 1
    else hi = mid
  }
  return lo
}

function firstIndexAfter(values: ArrayLike<number>, target: number): number {
  let lo = 0
  let hi = values.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (values[mid] <= target) lo = mid + 1
    else hi = mid
  }
  return lo
}
