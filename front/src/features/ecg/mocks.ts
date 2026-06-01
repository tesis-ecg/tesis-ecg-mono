/**
 * Generador determinístico de señal ECG sintética.
 *
 * Datos mock — borrar este archivo y eliminar imports en `api/ecgApi.ts`
 * cuando el endpoint real `GET /studies/:id/ecg` esté disponible (ver TES-32).
 *
 * La señal se compone de:
 * - Baseline isoeléctrica (DC en 0 mV)
 * - Modulación lenta por respiración (~0.3 Hz, amplitud ~0.1 mV)
 * - Complejos QRS sintéticos cada ~800 ms (75 bpm), con ondas P y T
 * - Ruido gaussiano de baja amplitud
 *
 * El seed (típicamente `studyId`) hace la señal reproducible: misma id →
 * misma señal en cada render. Esto permite verificar visualmente la página de
 * detalle del estudio sin que la señal cambie en cada recarga.
 */
import type { ECGSignal } from './types'

/**
 * PRNG xfnv1a + sfc32 — determinístico, suficientemente uniforme para señal
 * sintética. No usar para criptografía.
 */
function makeRng(seed: string): () => number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  let a = h >>> 0
  let b = (h ^ 0x9e3779b9) >>> 0
  let c = (h ^ 0x6a09e667) >>> 0
  let d = (h ^ 0xbb67ae85) >>> 0
  return function rng() {
    a |= 0
    b |= 0
    c |= 0
    d |= 0
    const t = (((a + b) | 0) + d) | 0
    d = (d + 1) | 0
    a = b ^ (b >>> 9)
    b = (c + (c << 3)) | 0
    c = (c << 21) | (c >>> 11)
    c = (c + t) | 0
    return (t >>> 0) / 4294967296
  }
}

/** Box-Muller para ruido gaussiano N(0, 1). */
function gaussian(rng: () => number): number {
  const u = Math.max(rng(), 1e-12)
  const v = rng()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/**
 * Forma de onda QRS centrada en `0` con ancho `widthSec`. Devuelve mV.
 * Aproximación gaussiana — no es clínicamente exacta pero visualmente correcta.
 */
function qrsWaveform(tFromCenterSec: number, widthSec: number, amplitudeMv: number): number {
  const sigma = widthSec / 4
  const x = tFromCenterSec / sigma
  return amplitudeMv * Math.exp(-0.5 * x * x)
}

/** Onda P (atrial) — baja amplitud, gaussiana ancha antes del QRS. */
function pWaveform(tFromCenterSec: number, widthSec: number, amplitudeMv: number): number {
  const sigma = widthSec / 3
  const x = tFromCenterSec / sigma
  return amplitudeMv * Math.exp(-0.5 * x * x)
}

/** Onda T (repolarización) — más ancha y baja después del QRS. */
function tWaveform(tFromCenterSec: number, widthSec: number, amplitudeMv: number): number {
  const sigma = widthSec / 2.5
  const x = tFromCenterSec / sigma
  return amplitudeMv * Math.exp(-0.5 * x * x)
}

export function mockEcgSignal(
  seed: string,
  durationSec = 3600,
  sampleRate = 250,
  startTimestamp = Date.UTC(2026, 0, 1, 10, 0, 0),
): ECGSignal {
  const rng = makeRng(seed || 'default')
  const totalSamples = durationSec * sampleRate
  const samples = new Float32Array(totalSamples)

  // 75 bpm con jitter de ±20 ms por latido para que no sea perfectamente periódico.
  const baseBeatIntervalSec = 60 / 75

  // QRS amplitude varía ligeramente por seed (0.9 - 1.1 mV).
  const qrsAmp = 0.9 + rng() * 0.2
  const pAmp = qrsAmp * 0.15
  const tAmp = qrsAmp * 0.3

  const qrsWidth = 0.08 // 80 ms
  const pWidth = 0.08
  const tWidth = 0.16

  // Pre-calcular los timestamps de los QRS (más eficiente que buscar el más
  // cercano en cada sample).
  const qrsCenters: number[] = []
  let nextBeat = 0.4 + rng() * 0.4 // primer latido entre 0.4 y 0.8 s
  while (nextBeat < durationSec) {
    qrsCenters.push(nextBeat)
    const jitter = (rng() - 0.5) * 0.04 // ±20 ms
    nextBeat += baseBeatIntervalSec + jitter
  }

  // Helper: encontrar el QRS más cercano a `tSec` (búsqueda por proximidad usando
  // índice estimado, evita O(N) en cada sample).
  let qrsIdx = 0

  for (let i = 0; i < totalSamples; i++) {
    const tSec = i / sampleRate

    // Avanzar el índice del QRS de referencia.
    while (qrsIdx + 1 < qrsCenters.length && qrsCenters[qrsIdx + 1] < tSec - 0.4) {
      qrsIdx++
    }

    let value = 0
    // Sumar la contribución de los QRS cercanos (anterior, actual, siguiente).
    for (let k = qrsIdx; k < Math.min(qrsIdx + 2, qrsCenters.length); k++) {
      const center = qrsCenters[k]
      const dt = tSec - center
      if (Math.abs(dt) < 0.4) {
        // Onda P antes del QRS (~120 ms antes del centro).
        value += pWaveform(dt + 0.12, pWidth, pAmp)
        // QRS.
        value += qrsWaveform(dt, qrsWidth, qrsAmp)
        // Onda T después del QRS (~200 ms después).
        value += tWaveform(dt - 0.2, tWidth, tAmp)
      }
    }

    // Respiración (modulación lenta).
    value += 0.08 * Math.sin(2 * Math.PI * 0.3 * tSec)

    // Ruido gaussiano de baja amplitud.
    value += 0.02 * gaussian(rng)

    samples[i] = value
  }

  return {
    sampleRate,
    samples,
    startTimestamp,
  }
}
