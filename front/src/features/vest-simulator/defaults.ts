import { SAMPLE_RATE_HZ, readHeader } from './codec/frame'
import { encodeSamples } from './codec/riceEncoder'
import { DEFAULT_SIGNAL_CONFIG, generateSignal, type SignalConfig } from './codec/signal'
import type { VestConfig } from './types'

let counter = 0

export function makeVestConfig(overrides: Partial<VestConfig> = {}): VestConfig {
  counter += 1
  return {
    id: `vest-${Date.now()}-${counter}`,
    label: `Chaleco ${counter}`,
    deviceId: '',
    serial: '',
    apiKey: '',
    // 10 min por default y no 60: un lote de una hora tarda en generarse y la
    // idea es poder iterar. El botón de "1 h" está a un clic para medir el
    // volumen real.
    batchMinutes: 10,
    batchCount: 3,
    cadence: { kind: 'instant' },
    signal: {
      ...DEFAULT_SIGNAL_CONFIG,
      seed: Math.floor(Math.random() * 1_000_000),
      sampleRateHz: SAMPLE_RATE_HZ,
      durationSec: 600,
    },
    frames: {
      corruptCrcPct: 0,
      duplicatePct: 0,
      dropPct: 0,
      rebootAtBatch: 0,
      // Prendido por default: esto es un simulador de banco y el backend marca
      // el estudio como no clínico. Apagarlo es una decisión consciente.
      simulated: true,
      shuffle: false,
    },
    network: {
      truncateBodyPct: 0,
      invalidApiKey: false,
      unknownSerial: false,
      omitUptime: false,
      maxRetries: 2,
    },
    ...overrides,
  }
}

/** Segundos de señal que se comprimen de verdad para calibrar la estimación. */
const PROBE_SECONDS = 8

const probeCache = new Map<string, number>()

/**
 * Muestras que entran en una trama con esta configuración de señal.
 *
 * Se mide comprimiendo unos segundos reales en vez de usar una constante. El
 * caudal depende de cuánto comprima el Rice, y eso depende del ruido y de la
 * amplitud configurados: con la constante fija de 1,8 tramas/s el panel decía
 * "~6.480 tramas" para una hora que en la práctica daba 8.576 — un 32 % de
 * diferencia, callado, justo en el número que se usa para dimensionar el volumen.
 *
 * La última trama de la sonda se descarta porque cierra a medias por flush y
 * bajaría el promedio.
 */
function samplesPerFrame(signal: SignalConfig): number {
  const key = [
    signal.sampleRateHz,
    signal.nChannels,
    signal.baseBpm,
    signal.bpmVariability,
    signal.qrsAmplitudeUV,
    signal.noiseUV,
    signal.seed,
  ].join('|')
  const cached = probeCache.get(key)
  if (cached !== undefined) return cached

  const probe = generateSignal({ ...signal, durationSec: PROBE_SECONDS })
  const frames = encodeSamples(probe.samples, { nChannels: signal.nChannels })
  const full = frames.slice(0, -1)
  const measured = full.length
    ? full.reduce((total, frame) => total + readHeader(frame).nSamples, 0) / full.length
    : probe.samples.length

  probeCache.set(key, measured)
  return measured
}

/** Estimación del peso del lote antes de generarlo, para la UI. */
export function estimateBatch(config: VestConfig): {
  samples: number
  uncompressedBytes: number
  estimatedFrames: number
  estimatedBytes: number
} {
  const samples = Math.round(config.batchMinutes * 60 * config.signal.sampleRateHz)
  const uncompressedBytes = samples * 4 * config.signal.nChannels
  const estimatedFrames = Math.ceil(samples / samplesPerFrame(config.signal))
  return { samples, uncompressedBytes, estimatedFrames, estimatedBytes: estimatedFrames * 256 }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}
