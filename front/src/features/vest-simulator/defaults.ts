import { SAMPLE_RATE_HZ, readHeader } from './codec/frame'
import { encodeSamples } from './codec/riceEncoder'
import {
  DEFAULT_SIGNAL_PROFILE,
  EPISODE_META,
  generateEcg,
  initialGeneratorState,
  type Episode,
  type EpisodeKind,
  type SignalProfile,
} from './codec/signal'
import { BRIDGE_POST_FRAMES } from './deviceClock'
import type { VestConfig } from './types'

let counter = 0

export const DEFAULT_NETWORK: VestConfig['network'] = {
  postFrames: BRIDGE_POST_FRAMES,
  graceSeconds: 60,
  rssiDbm: -62,
  truncateBodyPct: 0,
  invalidApiKey: false,
  unknownSerial: false,
  omitUptime: false,
  noSntp: false,
  lostBootTable: false,
}

/** Un episodio nuevo con los valores por defecto de su tipo. */
export function makeEpisode(kind: EpisodeKind, overrides: Partial<Episode> = {}): Episode {
  const meta = EPISODE_META[kind]
  return {
    id: `ep-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    kind,
    batch: 1,
    startSec: 60,
    durationSec: meta.defaultDurationSec,
    value: meta.defaultValue,
    ...overrides,
  }
}

export function makeVestConfig(overrides: Partial<VestConfig> = {}): VestConfig {
  counter += 1
  return {
    id: `vest-${Date.now()}-${counter}`,
    label: `Chaleco ${counter}`,
    deviceId: '',
    serial: '',
    apiKey: '',
    // 10 min: es la ventana de envío del equipo (`HOLTER_LINK_BATCH_INTERVAL_MS`).
    batchMinutes: 10,
    batchCount: 3,
    cadence: { kind: 'instant' },
    signal: {
      ...DEFAULT_SIGNAL_PROFILE,
      seed: Math.floor(Math.random() * 1_000_000),
    },
    episodes: [],
    pendingInjections: [],
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
    placementOk: true,
    network: { ...DEFAULT_NETWORK },
    ...overrides,
  }
}

/** Segundos de señal que se comprimen de verdad para calibrar la estimación. */
const PROBE_SECONDS = 12

const probeCache = new Map<string, number>()

/**
 * Muestras que entran en una trama con esta configuración de señal.
 *
 * Se mide comprimiendo unos segundos reales en vez de usar una constante. El
 * caudal depende de cuánto comprima el Rice, y eso depende sobre todo de la red
 * de 50 Hz que le entra a la entrada: entre ~1,5 tramas/s con gel limpio y ~3,9
 * al lado del router, medido sobre la placa.
 *
 * La última trama de la sonda se descarta porque cierra a medias por flush y
 * bajaría el promedio.
 */
function samplesPerFrame(signal: SignalProfile): number {
  const key = [
    signal.electrode,
    signal.environment,
    signal.baseBpm,
    signal.rAmplitudeUV,
    signal.respirationPerMin,
    signal.seed,
  ].join('|')
  const cached = probeCache.get(key)
  if (cached !== undefined) return cached

  const probe = generateEcg({
    profile: signal,
    durationSec: PROBE_SECONDS,
    episodes: [],
    state: initialGeneratorState(signal),
    startT0Ms: 0,
    wallStartEpochMs: Date.now(),
  })
  const frames = encodeSamples(probe.samples)
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
  const samples = Math.round(config.batchMinutes * 60 * SAMPLE_RATE_HZ)
  const uncompressedBytes = samples * 4
  const estimatedFrames = Math.ceil(samples / samplesPerFrame(config.signal))
  return { samples, uncompressedBytes, estimatedFrames, estimatedBytes: estimatedFrames * 256 }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}
