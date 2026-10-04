/**
 * Morfología de un latido en una derivación tipo II.
 *
 * Cada onda es una gaussiana asimétrica ubicada respecto del pico R. Es el
 * modelo de McSharry (ECGSYN) sin la integración de la ecuación diferencial:
 * para el visor y para el detector del backend lo que importa es la forma y
 * los intervalos, no la dinámica de fase.
 *
 * Proporciones medidas por el canal 2 de la placa (`capturas/README`): P/R
 * entre 0,08 y 0,10, T/R entre 0,20 y 0,38.
 */

/**
 * - `N`: sinusal.
 * - `V`: extrasístole ventricular. Sin P, QRS ancho, T opuesta.
 * - `A`: extrasístole auricular. P distinta y prematura, QRS normal.
 * - `F`: conducido durante una fibrilación auricular. Sin P.
 */
export type BeatKind = 'N' | 'V' | 'A' | 'F'

export interface Beat {
  /** Hora del pico R, en segundos del reloj del generador. */
  r: number
  kind: BeatKind
  /** RR que lo precede, en segundos. Fija el QT. */
  rr: number
  /** Amplitud de R en µV, ya modulada por la respiración. */
  amp: number
}

interface Wave {
  center: number
  sigmaLeft: number
  sigmaRight: number
  /** Fracción de la amplitud de R. */
  height: number
}

/** Ventana que ocupa un latido alrededor de su R. */
export const BEAT_BEFORE_SEC = 0.42
export const BEAT_AFTER_SEC = 0.78

/**
 * QT por la fórmula de Hodges: `QTc + 1,75 ms × (FC − 60)` despejado. Con un QT
 * fijo, una taquicardia a 160 lpm pondría la T encima de la P siguiente.
 */
export function qtSeconds(rr: number): number {
  const hr = 60 / Math.max(0.25, rr)
  return Math.min(0.48, Math.max(0.26, 0.41 - 0.00175 * (hr - 60)))
}

function waves(beat: Beat): Wave[] {
  const qt = qtSeconds(beat.rr)
  // La T termina un QT después del inicio de Q (~45 ms antes de R). Su pico
  // queda a ~2,2 sigmas del final.
  const tPeak = qt - 0.045 - 2.2 * 0.036
  const qrsT: Wave[] = [
    { center: -0.03, sigmaLeft: 0.0075, sigmaRight: 0.0075, height: -0.08 },
    { center: 0, sigmaLeft: 0.0105, sigmaRight: 0.0105, height: 1 },
    { center: 0.03, sigmaLeft: 0.0095, sigmaRight: 0.0095, height: -0.2 },
    { center: tPeak, sigmaLeft: 0.056, sigmaRight: 0.036, height: 0.3 },
    { center: tPeak + 0.16, sigmaLeft: 0.025, sigmaRight: 0.025, height: 0.025 },
  ]
  switch (beat.kind) {
    case 'N':
      return [{ center: -0.165, sigmaLeft: 0.026, sigmaRight: 0.022, height: 0.1 }, ...qrsT]
    case 'A':
      // P ectópica: más chica, invertida y más cerca del QRS.
      return [{ center: -0.13, sigmaLeft: 0.02, sigmaRight: 0.018, height: -0.06 }, ...qrsT]
    case 'F':
      return qrsT
    case 'V':
      return [
        { center: 0, sigmaLeft: 0.032, sigmaRight: 0.03, height: 1.25 },
        { center: 0.075, sigmaLeft: 0.028, sigmaRight: 0.03, height: -0.45 },
        { center: 0.33, sigmaLeft: 0.075, sigmaRight: 0.06, height: -0.4 },
      ]
  }
}

/**
 * Suma el latido en `out`, que cubre `[t0, t0 + out.length / fs)`. Solo toca
 * las muestras de su ventana, así que un latido que cruza el borde del lote
 * aporta lo suyo de cada lado.
 */
export function renderBeat(out: Float64Array, t0: number, fs: number, beat: Beat): void {
  const first = Math.max(0, Math.ceil((beat.r - BEAT_BEFORE_SEC - t0) * fs))
  const last = Math.min(out.length - 1, Math.floor((beat.r + BEAT_AFTER_SEC - t0) * fs))
  if (first > last) return
  const parts = waves(beat)
  for (let i = first; i <= last; i++) {
    const dt = t0 + i / fs - beat.r
    let value = 0
    for (const wave of parts) {
      const offset = dt - wave.center
      const sigma = offset < 0 ? wave.sigmaLeft : wave.sigmaRight
      const z = offset / sigma
      if (z > -5 && z < 5) value += wave.height * Math.exp(-0.5 * z * z)
    }
    out[i] += value * beat.amp
  }
}
