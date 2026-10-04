/**
 * Escala de papel de ECG: 25 mm/s y 10 mm/mV.
 *
 * Un ECG se lee sobre una escala fija. El ancho del QRS, el PR y el QT se leen
 * en cuadros —120 ms son 3 cuadros chicos— y un desnivel del ST o un criterio
 * de voltaje se leen en milímetros a 10 mm/mV. Sin escala fija, la misma onda se
 * ve más alta o más baja según qué más haya en la ventana, y más ancha o más
 * angosta según el ancho de la pantalla.
 *
 * Hasta septiembre de 2026 el visor recibía `paperSpeed` y `amplitude` y los
 * descartaba en la línea siguiente. El eje Y era `auto` (uPlot elegía el rango a
 * partir de los datos visibles, así que un artefacto grande aplastaba el
 * trazado) y el eje X mostraba 10 s repartidos en el ancho que tuviera el
 * contenedor, o sea 25 mm/s solo por casualidad del maquetado.
 *
 * **Lo que este módulo garantiza y lo que no.** La proporción es exacta en
 * cualquier monitor: `pxPorSegundo / pxPorMv === paperSpeed / amplitude`, o sea
 * que con 25/10 el cuadro de 40 ms × 0,1 mV queda cuadrado. Los milímetros
 * físicos NO se pueden garantizar en pantalla: el navegador trabaja en px CSS de
 * 1/96 de pulgada nominal y cuánto mide eso de verdad depende del monitor y del
 * escalado del sistema. Para milímetros reales está la vista de impresión, que
 * usa unidades físicas (`printable.css`).
 */

/** Velocidades de barrido de un electrocardiógrafo, en mm/s. */
export const PAPER_SPEEDS = [25, 50] as const
/** Ganancias de un electrocardiógrafo, en mm/mV. */
export const AMPLITUDES = [5, 10, 20] as const

export type PaperSpeed = (typeof PAPER_SPEEDS)[number]
export type Amplitude = (typeof AMPLITUDES)[number]

/**
 * Ganancia automática: el rango vertical se ajusta a la señal visible.
 *
 * Existe para las pruebas de banco, donde el trazado puede salirse de cualquier
 * ganancia fija (un offset grande, un artefacto, una señal sin calibrar) y lo
 * primero que hace falta es **verla entera**. No sirve para medir: los mm/mV
 * dejan de ser fijos y el rótulo lo dice.
 */
export const AUTO_AMPLITUDE = 'auto' as const
export type AmplitudeMode = Amplitude | typeof AUTO_AMPLITUDE

/** El estándar de diagnóstico para adultos. */
export const DEFAULT_PAPER_SPEED: PaperSpeed = 25
export const DEFAULT_AMPLITUDE: Amplitude = 10

/** Lado del cuadro chico del papel de ECG. */
export const SMALL_BOX_MM = 1
/** Lado del cuadro grande: 200 ms × 0,5 mV a escala estándar. */
export const LARGE_BOX_MM = 5

/**
 * px CSS por milímetro asumiendo los 96 dpi nominales del navegador.
 *
 * Es el fallback de `measurePxPerMm()`. No es un milímetro real y no pretende
 * serlo: lo que importa para leer un ECG en pantalla es la proporción, y ésa
 * sale exacta con cualquier valor mientras sea el mismo en los dos ejes.
 */
export const NOMINAL_PX_PER_MM = 96 / 25.4

/**
 * Mide cuántos px CSS ocupa 1 mm según el navegador.
 *
 * Se hace con un elemento real y no con la constante porque un navegador con
 * zoom, o un monitor con escalado, puede reportar otra cosa — y si va a estar
 * mal, que esté mal de la misma forma en los dos ejes. Devuelve el nominal en
 * SSR o si la medición da algo absurdo.
 */
export function measurePxPerMm(): number {
  if (typeof document === 'undefined') return NOMINAL_PX_PER_MM
  const probe = document.createElement('div')
  probe.style.cssText = 'position:absolute;visibility:hidden;height:100mm;width:1px;top:-1000mm'
  document.body.appendChild(probe)
  const measured = probe.getBoundingClientRect().height / 100
  probe.remove()
  return Number.isFinite(measured) && measured > 0 ? measured : NOMINAL_PX_PER_MM
}

export interface PaperScale {
  paperSpeed: number
  /**
   * Ganancia en mm/mV. En modo automático queda en la estándar y solo gradúa la
   * retícula (0,1 mV el cuadro chico); el rango vertical lo pone la señal.
   */
  amplitude: number
  /** `true` cuando el rango vertical sigue a la señal en vez de a la ganancia. */
  autoAmplitude: boolean
  pxPerMm: number
  /** px por segundo del eje de tiempo. */
  pxPerSec: number
  /** px por milivoltio del eje de amplitud. */
  pxPerMv: number
}

export function paperScale(
  paperSpeed: number,
  amplitude: number | typeof AUTO_AMPLITUDE,
  pxPerMm: number,
): PaperScale {
  const autoAmplitude = amplitude === AUTO_AMPLITUDE
  const gain = autoAmplitude ? DEFAULT_AMPLITUDE : amplitude
  return {
    paperSpeed,
    amplitude: gain,
    autoAmplitude,
    pxPerMm,
    pxPerSec: paperSpeed * pxPerMm,
    pxPerMv: gain * pxPerMm,
  }
}

/**
 * Cuántos segundos entran en `widthPx` a esta escala.
 *
 * Es la inversión de la regla vieja y es el punto entero del cambio: antes el
 * viewer fijaba 10 s y los repartía en el ancho que hubiera, así que agrandar la
 * ventana estiraba la misma onda. Ahora la escala manda y el ancho decide
 * cuántos segundos se ven: agrandar muestra **más señal**, no la misma más
 * grande.
 */
export function visibleSeconds(scale: PaperScale, widthPx: number): number {
  if (widthPx <= 0 || scale.pxPerSec <= 0) return 0
  return widthPx / scale.pxPerSec
}

/** Cuántos mV entran verticalmente en `heightPx` a esta escala. */
export function visibleMillivolts(scale: PaperScale, heightPx: number): number {
  if (heightPx <= 0 || scale.pxPerMv <= 0) return 0
  return heightPx / scale.pxPerMv
}

/**
 * El rango vertical fijo, centrado en `centerMv`.
 *
 * Reemplaza a `scales.y.auto`. El centro es la línea de base de la ventana
 * visible y no 0: el front-end es DC-acoplado, así que el potencial de media
 * celda de los electrodos puede correr el trazado decenas de milivoltios sin que
 * eso sea una falla (`INTEGRACION.md` §3.2, y es la razón de que `raw_uV` sea
 * int32 y no int16). Anclar el rango en 0 dejaría al paciente con offset fuera
 * de pantalla.
 *
 * Lo que sí es fijo es el **span**: no depende de lo que haya dentro de la
 * ventana, así que un artefacto grande ya no aplasta el trazado.
 */
export function verticalRange(
  scale: PaperScale,
  heightPx: number,
  centerMv: number,
): [number, number] {
  const span = visibleMillivolts(scale, heightPx)
  if (span <= 0) return [centerMv - 1, centerMv + 1]
  return [centerMv - span / 2, centerMv + span / 2]
}

/**
 * Línea de base de un tramo de muestras: la mediana, no el promedio.
 *
 * La mediana ignora los artefactos. Con el promedio, una saturación del ADC en
 * medio de la ventana correría el centro y sacaría el trazado de pantalla, que
 * es exactamente el problema que esta escala vino a resolver.
 */
export function baselineMv(samples: Float32Array, from: number, to: number): number {
  const start = Math.max(0, Math.min(from, samples.length))
  const end = Math.max(start, Math.min(to, samples.length))
  if (end === start) return 0

  // Muestreo acotado: una ventana de 10 s a 500 SPS son 5000 puntos y esto corre
  // en cada cambio de viewport. Con ~600 alcanza de sobra para una mediana.
  const stride = Math.max(1, Math.floor((end - start) / 600))
  const values: number[] = []
  for (let i = start; i < end; i += stride) {
    const value = samples[i]
    if (Number.isFinite(value)) values.push(value)
  }
  if (values.length === 0) return 0
  values.sort((a, b) => a - b)
  const middle = values.length >> 1
  return values.length % 2 === 0 ? (values[middle - 1] + values[middle]) / 2 : values[middle]
}

/**
 * Span mínimo del modo automático, en mV.
 *
 * Sin piso, una señal plana (electrodo suelto, sin pulso) se estiraría hasta
 * llenar la altura y el ruido de cuantización parecería actividad.
 */
const AUTO_MIN_SPAN_MV = 1
/** Margen arriba y abajo del modo automático, como fracción del span. */
const AUTO_PADDING = 0.08

/**
 * Rango vertical del modo automático: mínimo y máximo del tramo, con margen.
 *
 * A diferencia de `baselineMv`, acá no se muestrea con salto: el objetivo es que
 * **ningún** pico quede fuera de pantalla, y un QRS dura pocas muestras.
 * Devuelve `null` si el tramo no tiene muestras finitas.
 */
export function autoVerticalRange(
  samples: Float32Array,
  from: number,
  to: number,
): [number, number] | null {
  const start = Math.max(0, Math.min(from, samples.length))
  const end = Math.max(start, Math.min(to, samples.length))
  let min = Infinity
  let max = -Infinity
  for (let i = start; i < end; i++) {
    const value = samples[i]
    if (!Number.isFinite(value)) continue
    if (value < min) min = value
    if (value > max) max = value
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null
  const center = (min + max) / 2
  const span = Math.max(max - min, AUTO_MIN_SPAN_MV) * (1 + AUTO_PADDING * 2)
  return [center - span / 2, center + span / 2]
}

/**
 * Centro vertical con el desplazamiento del médico aplicado.
 *
 * Se acota a la extensión de la señal visible: el centro nunca se va más allá
 * del pico más alto ni del valle más bajo, así que siempre queda algo de traza
 * en pantalla para orientarse.
 */
export function pannedCenterMv(
  baseline: number,
  offsetMv: number,
  extent: readonly [number, number],
): number {
  return Math.min(Math.max(baseline + offsetMv, extent[0]), extent[1])
}

/** El rótulo que va en pantalla, como en cualquier electrocardiógrafo. */
export function scaleLabel(scale: PaperScale): string {
  return `${scale.paperSpeed} mm/s · ${amplitudeLabel(scale.autoAmplitude ? AUTO_AMPLITUDE : scale.amplitude)}`
}

export function amplitudeLabel(amplitude: number | typeof AUTO_AMPLITUDE): string {
  return amplitude === AUTO_AMPLITUDE ? 'Amplitud automática' : `${amplitude} mm/mV`
}

/**
 * ¿El viewport coincide con la escala declarada?
 *
 * El zoom libre (Ctrl + rueda, el mini-mapa) sirve para **navegar**, no para
 * medir. Cuando el rango visible deja de corresponder a `paperSpeed`, el rótulo
 * tiene que decirlo: si no, alguien puede medir un QT sobre una escala que no es
 * la que el cartel afirma. La tolerancia absorbe el redondeo a px enteros.
 */
export function matchesScale(scale: PaperScale, widthPx: number, spanSec: number): boolean {
  const expected = visibleSeconds(scale, widthPx)
  if (expected <= 0 || spanSec <= 0) return false
  return Math.abs(spanSec - expected) / expected < 0.02
}
