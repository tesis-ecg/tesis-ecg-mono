/**
 * Generador de señal ECG para el simulador.
 *
 * No pretende ser fisiológicamente exacto: pretende ser **representativo en
 * forma, amplitud y comprimibilidad**, que es lo que hace que el volumen de
 * datos que sube el simulador se parezca al del equipo real.
 *
 * Todo es determinista a partir de una semilla, así que dos corridas con la
 * misma configuración producen exactamente los mismos bytes — indispensable
 * para regenerar el fixture dorado que verifica el codec contra el backend.
 */

import {
  FLAG_ADC_SATURATED,
  FLAG_EVENT_MARKER,
  FLAG_LEAD_OFF,
  FLAG_R_PEAK,
  FLAG_RLD_OFF,
  FLAG_SQI_SHIFT,
  SAMPLE_RATE_HZ,
  SQ_BAD,
  SQ_GOOD,
  SQ_MARGINAL,
  STEP_MS,
} from './frame'
import type { EcgSample } from './riceEncoder'

/** PRNG determinista (mulberry32). No hace falta calidad criptográfica. */
export function makeRng(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let t = state
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Tramo con una anomalía aplicada, en segundos desde el inicio del lote. */
export interface AnomalySpan {
  startSec: number
  durationSec: number
}

/**
 * Tramo con un foco ectópico activo.
 *
 * Un latido ectópico son **tres cosas juntas**, no solo una forma distinta:
 * llega antes de tiempo, su QRS es más ancho, y lo sigue una pausa
 * compensatoria. Sin la prematuridad sería indistinguible de un artefacto de
 * movimiento — que es exactamente la confusión que el motor de detección tiene
 * que resolver, y por eso el simulador tiene que producir las tres.
 */
export interface EctopicSpan extends AnomalySpan {
  /** 2 = bigeminismo, 3 = trigeminismo, 12 = uno de cada doce latidos. */
  everyNBeats: number
  /** Acoplamiento: 0,6 = el ectópico llega al 60 % del R-R esperado. */
  coupling: number
  /** Ancho del QRS relativo al normal. 3 ≈ 180 ms, típico ventricular. */
  widthFactor: number
  amplitudeFactor: number
  /** La onda T de un ectópico ventricular es de polaridad opuesta al QRS. */
  invertT: boolean
}

/** Tramo con una frecuencia cardíaca distinta de la basal. */
export interface RateSpan extends AnomalySpan {
  bpm: number
}

export interface SignalConfig {
  seed: number
  durationSec: number
  sampleRateHz: number
  nChannels: number
  /** Frecuencia cardíaca base en lpm. */
  baseBpm: number
  /** Variabilidad de la FC, en lpm (±). */
  bpmVariability: number
  /** Amplitud del complejo QRS en µV. */
  qrsAmplitudeUV: number
  /** Ruido de banda ancha en µV RMS. */
  noiseUV: number
  /**
   * Offset de continua en µV. El front-end es DC-acoplado y las normas de ECG
   * exigen tolerar hasta ±300 mV de potencial de media celda.
   */
  baselineOffsetUV: number
  leadOffSpans: AnomalySpan[]
  rldOffSpans: AnomalySpan[]
  saturatedSpans: AnomalySpan[]
  /** Tramos marcados como NO analizables (SQI = 1). */
  unanalyzableSpans: AnomalySpan[]
  /** Instantes en los que el paciente apretó el botón de síntoma. */
  symptomMarkersSec: number[]
  /** Focos ectópicos: lo que ejercita la Etapa 2 del motor de detección. */
  ectopicSpans: EctopicSpan[]
  /** Latidos salteados: el R-R pasa a valer el doble. */
  pauseSpans: AnomalySpan[]
  /** Taquicardia o bradicardia programada. */
  rateSpans: RateSpan[]
}

/**
 * Lo que tarda el detector del MCU en confirmar un latido después del pico:
 * 160 ms del FIR de 161 taps, 40 ms de la cascada de detección y hasta 100 ms
 * de ventana de confirmación. Medido por el equipo de firmware sobre el chaleco
 * el 2026-09-03. No es un parámetro del escenario: es una propiedad del
 * hardware, y el simulador la imita para que la demo pase por el mismo camino
 * que la señal real.
 */
const FIRMWARE_R_PEAK_LAG_MS = 250

export const DEFAULT_SIGNAL_CONFIG: SignalConfig = {
  seed: 1,
  durationSec: 60,
  sampleRateHz: SAMPLE_RATE_HZ,
  nChannels: 1,
  baseBpm: 72,
  bpmVariability: 6,
  qrsAmplitudeUV: 1100,
  noiseUV: 25,
  baselineOffsetUV: 0,
  leadOffSpans: [],
  rldOffSpans: [],
  saturatedSpans: [],
  unanalyzableSpans: [],
  symptomMarkersSec: [],
  // Vacíos por defecto, y eso es un requisito duro y no una comodidad: con los
  // tres vacíos `generateSignal` no puede consumir ni una llamada extra del
  // PRNG, porque `GOLDEN_CONFIG` hace spread de estos valores y el fixture
  // dorado que verifica el codec vive en `back/tests/fixtures/frames_golden.bin`
  // — romperlo rompe el CI del backend, no solo el del front.
  ectopicSpans: [],
  pauseSpans: [],
  rateSpans: [],
}

function inSpans(sec: number, spans: AnomalySpan[]): boolean {
  return spans.some((span) => sec >= span.startSec && sec < span.startSec + span.durationSec)
}

function spanAt<T extends AnomalySpan>(sec: number, spans: T[]): T | null {
  return (
    spans.find((span) => sec >= span.startSec && sec < span.startSec + span.durationSec) ?? null
  )
}

/**
 * Un latido: onda P, complejo QRS y onda T sobre una línea de base con deriva
 * lenta. Las anchuras son las clínicas típicas (P ~80 ms, QRS ~90 ms, T ~160 ms).
 */
function beatShape(phase: number, amplitudeUV: number): number {
  const gauss = (center: number, width: number, height: number): number =>
    height * Math.exp(-(((phase - center) / width) ** 2))

  return (
    gauss(0.16, 0.035, amplitudeUV * 0.14) + // P
    gauss(0.29, 0.008, -amplitudeUV * 0.18) + // Q
    gauss(0.3, 0.009, amplitudeUV) + // R
    gauss(0.32, 0.012, -amplitudeUV * 0.25) + // S
    gauss(0.47, 0.06, amplitudeUV * 0.3) // T
  )
}

/**
 * Latido ectópico: sin onda P, QRS ancho y monofásico, onda T opuesta.
 *
 * Es una función aparte y no `beatShape` con parámetros con valor por defecto,
 * para que el camino normal quede **byte a byte idéntico** al de antes de que
 * los ectópicos existieran. Un `* 1` de más basta para mover un redondeo.
 */
function ectopicShape(phase: number, amplitudeUV: number, span: EctopicSpan): number {
  const gauss = (center: number, width: number, height: number): number =>
    height * Math.exp(-(((phase - center) / width) ** 2))
  const amplitude = amplitudeUV * span.amplitudeFactor
  const width = span.widthFactor

  return (
    gauss(0.29, 0.008 * width, -amplitude * 0.25) +
    gauss(0.3, 0.009 * width, amplitude) +
    gauss(0.33, 0.012 * width, -amplitude * 0.3) +
    gauss(0.5, 0.07, (span.invertT ? -1 : 1) * amplitude * 0.3)
  )
}

export interface GeneratedSignal {
  samples: EcgSample[]
  beats: number
}

export function generateSignal(config: SignalConfig, startTimestampMs = 0): GeneratedSignal {
  const rng = makeRng(config.seed)
  const stepMs = Math.floor(1000 / config.sampleRateHz) || STEP_MS
  const total = Math.round(config.durationSec * config.sampleRateHz)
  const samples: EcgSample[] = new Array(total)

  let beatPhase = 0
  let beatPeriodSec = 60 / config.baseBpm
  let beats = 0
  let ectopic: EctopicSpan | null = null
  const consumedPauses = new Set<number>()
  const markerSamples = new Set(
    config.symptomMarkersSec.map((sec) => Math.round(sec * config.sampleRateHz)),
  )
  // El detector del MCU confirma el latido con retardo, así que el flag se
  // agenda para más adelante y se emite cuando el bucle llega a esa muestra.
  const rPeakFlagSamples = new Set<number>()
  const rPeakLagSamples = Math.round((FIRMWARE_R_PEAK_LAG_MS / 1000) * config.sampleRateHz)

  for (let i = 0; i < total; i++) {
    const sec = i / config.sampleRateHz

    beatPhase += 1 / config.sampleRateHz / beatPeriodSec
    if (beatPhase >= 1) {
      beatPhase -= 1
      beats++
      // La FC se re-sortea latido a latido: sin variabilidad la señal sería
      // perfectamente periódica y comprimiría muchísimo mejor que la real.
      //
      // Esta llamada al PRNG va SIEMPRE y va PRIMERA. Todo lo que sigue solo
      // multiplica el período ya sorteado, así que con los tramos nuevos vacíos
      // la secuencia del generador queda idéntica y el fixture dorado no cambia.
      const jitter = (rng() * 2 - 1) * config.bpmVariability
      const rate = spanAt(sec, config.rateSpans)
      beatPeriodSec = 60 / Math.max(30, (rate ? rate.bpm : config.baseBpm) + jitter)

      const previousEctopic = ectopic
      const span = spanAt(sec, config.ectopicSpans)
      ectopic = span !== null && beats % span.everyNBeats === 0 ? span : null

      if (ectopic !== null) {
        beatPeriodSec *= ectopic.coupling
      } else if (previousEctopic !== null) {
        // Pausa compensatoria: el corazón "recupera" el tiempo que el ectópico
        // adelantó, así que el intervalo hasta el latido siguiente se alarga.
        beatPeriodSec *= 2 - previousEctopic.coupling
      }

      const pause = spanAt(sec, config.pauseSpans)
      if (pause !== null && !consumedPauses.has(pause.startSec)) {
        consumedPauses.add(pause.startSec)
        beatPeriodSec *= 2.4 // se saltea un latido entero
      }
    }

    const drift = Math.sin(sec * 0.35) * 40 // deriva lenta de línea de base
    const noise = (rng() * 2 - 1) * config.noiseUV
    const shape =
      ectopic !== null
        ? ectopicShape(beatPhase, config.qrsAmplitudeUV, ectopic)
        : beatShape(beatPhase, config.qrsAmplitudeUV)
    let value = config.baselineOffsetUV + drift + noise + shape

    let flags = 0
    const leadOff = inSpans(sec, config.leadOffSpans)
    if (leadOff) {
      flags |= FLAG_LEAD_OFF
      // Con un electrodo suelto la entrada queda flotando: lo que se graba es
      // interferencia acoplada, no ECG. Se graba igual y se MARCA — descartar
      // esas muestras sería borrar parte del registro.
      value = config.baselineOffsetUV + (rng() * 2 - 1) * 4000
    }
    if (inSpans(sec, config.rldOffSpans)) flags |= FLAG_RLD_OFF
    if (inSpans(sec, config.saturatedSpans)) {
      flags |= FLAG_ADC_SATURATED
      value = value > 0 ? 400_000 : -400_000
    }
    if (markerSamples.has(i)) flags |= FLAG_EVENT_MARKER

    // El pico R cae en el punto más alto del complejo, pero el equipo NO marca
    // ahí: marca la muestra en la que su detector confirma el latido, 250 ms
    // después. Emitir el flag sobre el pico haría que el bSQI del motor diera
    // perfecto en la demo y cero sobre señal real — que es exactamente lo que
    // venía pasando.
    const isRPeak =
      beatPhase >= 0.298 && beatPhase < 0.298 + 1 / config.sampleRateHz / beatPeriodSec
    if (isRPeak && !leadOff) rPeakFlagSamples.add(i + rPeakLagSamples)
    if (rPeakFlagSamples.has(i)) flags |= FLAG_R_PEAK

    // Con LEAD_OFF el índice de calidad no significa nada: el equipo no puede
    // sostener lo que "ve" en una entrada flotante. Va como NO analizable.
    let sqi = SQ_GOOD
    if (leadOff || inSpans(sec, config.unanalyzableSpans)) sqi = SQ_BAD
    else if (inSpans(sec, config.rldOffSpans)) sqi = SQ_MARGINAL
    flags |= sqi << FLAG_SQI_SHIFT

    // `| 0` y no solo `Math.round`: el firmware entrega int32, y en JS
    // `Math.round(-0.3)` da `-0`, que no es lo mismo que `0` en una comparación
    // estricta y ensuciaría cualquier round-trip exacto.
    const rounded = Math.round(value) | 0
    samples[i] = {
      timestampMs: startTimestampMs + i * stepMs,
      rawUV:
        config.nChannels === 1
          ? [rounded]
          : [rounded, Math.round(rounded * 0.6 + (rng() * 2 - 1) * config.noiseUV) | 0],
      flags,
    }
  }

  return { samples, beats }
}

/** Bytes que ocuparía la señal sin comprimir, para mostrar el ratio real. */
export function uncompressedBytes(config: SignalConfig): number {
  return Math.round(config.durationSec * config.sampleRateHz) * 4 * config.nChannels
}
