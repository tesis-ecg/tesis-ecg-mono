import type { AnomalySpan, SignalConfig } from './codec/signal'

export type { AnomalySpan, SignalConfig }

/** Cómo se espacian los envíos de lotes. */
export type Cadence =
  /** Todos los lotes uno atrás de otro, sin esperar. */
  | { kind: 'instant' }
  /** Tiempo real acelerado: un lote de 1 h cada `3600/factor` segundos. */
  | { kind: 'accelerated'; factor: number }
  /** Un lote por hora, como el equipo real. */
  | { kind: 'realtime' }

/** Anomalías que se aplican a nivel trama, no a nivel muestra. */
export interface FrameAnomalies {
  /** Porcentaje de tramas a las que se les rompe el CRC, también solo en el primer envío. */
  corruptCrcPct: number
  /** Porcentaje de tramas que se envían dos veces. */
  duplicatePct: number
  /**
   * Porcentaje de tramas que se pierden **en el primer envío**, dejando un hueco
   * en `seq`. El equipo las retransmite en el ciclo siguiente (go-back-N) y esa
   * vez viajan intactas: la pérdida es transitoria, no borra la grabación.
   */
  dropPct: number
  /** Número de lote en el que el equipo se reinicia (0 = nunca). */
  rebootAtBatch: number
  /** `hdrFlags` bit 3. Apagarlo simula datos "clínicos". */
  simulated: boolean
  /** Envía las tramas del cuerpo en orden aleatorio. */
  shuffle: boolean
}

/** Fallos del canal de red y de las credenciales. */
export interface NetworkFaults {
  /** Corta el cuerpo al X % antes de mandarlo (0 = no cortar). */
  truncateBodyPct: number
  /** Usa una API key inválida. */
  invalidApiKey: boolean
  /** Usa un serial que no existe. */
  unknownSerial: boolean
  /** Omite el header de uptime. */
  omitUptime: boolean
  /** Reintentos automáticos ante error, con backoff. */
  maxRetries: number
}

export interface VestConfig {
  id: string
  label: string
  deviceId: string
  serial: string
  apiKey: string
  /** Minutos de señal por lote. El equipo real manda 60. */
  batchMinutes: number
  /** Cuántos lotes enviar en esta corrida. */
  batchCount: number
  cadence: Cadence
  signal: SignalConfig
  frames: FrameAnomalies
  network: NetworkFaults
  /**
   * Si el chaleco está bien colocado sobre la piel.
   *
   * No entra en la generación de señal: es un estado del equipo que se reporta
   * por el canal corto (`POST /ingest/device-status`) y que el backend guarda
   * en la fila del Holter. Vive en la config —y no en las stats— para que
   * sobreviva a recargar la página, igual que la API key: el backend recuerda
   * la última colocación reportada y una pantalla que arrancara siempre en
   * "bien puesto" mentiría sobre el estado real.
   */
  placementOk: boolean
}

export type VestPhase = 'idle' | 'generating' | 'uploading' | 'waiting' | 'done' | 'error'

export interface VestStats {
  batchesSent: number
  framesGenerated: number
  framesSent: number
  framesAccepted: number
  framesRejected: number
  framesDuplicate: number
  /** Tramas grabadas y todavía sin confirmar: lo que queda en la SD del equipo. */
  framesPending: number
  /** Tramas que se cayeron del backlog por desborde. Pérdida real de señal. */
  framesLost: number
  bytesSent: number
  uncompressedBytes: number
  lastSeq: number
  bootId: number
  uptimeMs: number
  studyId: string | null
  lastStatus: number | null
  lastError: string | null
}

export interface LogEntry {
  at: number
  level: 'info' | 'warn' | 'error'
  message: string
}

export interface VestState {
  config: VestConfig
  phase: VestPhase
  stats: VestStats
  log: LogEntry[]
}

export const EMPTY_STATS: VestStats = {
  batchesSent: 0,
  framesGenerated: 0,
  framesSent: 0,
  framesAccepted: 0,
  framesRejected: 0,
  framesDuplicate: 0,
  framesPending: 0,
  framesLost: 0,
  bytesSent: 0,
  uncompressedBytes: 0,
  lastSeq: -1,
  bootId: 0,
  uptimeMs: 0,
  studyId: null,
  lastStatus: null,
  lastError: null,
}

/** Ratio de compresión medido, o `null` si todavía no se generó nada. */
export function compressionRatio(stats: VestStats): number | null {
  if (stats.bytesSent === 0) return null
  return stats.uncompressedBytes / stats.bytesSent
}
