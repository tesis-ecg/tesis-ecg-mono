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
}

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
}

function inSpans(sec: number, spans: AnomalySpan[]): boolean {
  return spans.some((span) => sec >= span.startSec && sec < span.startSec + span.durationSec)
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
  const markerSamples = new Set(
    config.symptomMarkersSec.map((sec) => Math.round(sec * config.sampleRateHz)),
  )

  for (let i = 0; i < total; i++) {
    const sec = i / config.sampleRateHz

    beatPhase += 1 / config.sampleRateHz / beatPeriodSec
    if (beatPhase >= 1) {
      beatPhase -= 1
      beats++
      // La FC se re-sortea latido a latido: sin variabilidad la señal sería
      // perfectamente periódica y comprimiría muchísimo mejor que la real.
      const jitter = (rng() * 2 - 1) * config.bpmVariability
      beatPeriodSec = 60 / Math.max(30, config.baseBpm + jitter)
    }

    const drift = Math.sin(sec * 0.35) * 40 // deriva lenta de línea de base
    const noise = (rng() * 2 - 1) * config.noiseUV
    let value =
      config.baselineOffsetUV + drift + noise + beatShape(beatPhase, config.qrsAmplitudeUV)

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

    // El pico R cae en el punto más alto del complejo.
    const isRPeak =
      beatPhase >= 0.298 && beatPhase < 0.298 + 1 / config.sampleRateHz / beatPeriodSec
    if (isRPeak && !leadOff) flags |= FLAG_R_PEAK

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
