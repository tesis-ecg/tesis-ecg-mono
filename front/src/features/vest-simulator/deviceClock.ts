/**
 * Reloj, cursor y flash de un chaleco simulado.
 *
 * Vive fuera de la corrida a propósito. `seq` no es un número de orden
 * decorativo: es la identidad de la trama, y el backend descarta como duplicado
 * todo lo que llegue con un `seq` ya confirmado (`_ack_window` en
 * `ingest_service.py`). Ese dedupe existe porque el firmware libera la flash
 * recién con el ACK: si el ACK se pierde en el camino, el equipo reenvía un lote
 * que el backend ya guardó, y sin dedupe esa señal entraría dos veces en el
 * estudio.
 *
 * Las reglas que sostienen este módulo:
 *
 * 1. **El cursor no se reinicia entre corridas.** Un equipo real vuelve a
 *    `t0 = 0` solo cuando se reinicia, y ahí cambia el `bootId`; la `seq` sigue.
 *
 * 2. **Grabar y transmitir son cosas distintas.** El equipo graba en la flash
 *    con o sin WiFi, y una trama se borra recién cuando el backend la confirma
 *    (`INTEGRACION.md` §4.6: go-back-N desde la más vieja sin confirmar). La
 *    flash sobrevive al reinicio: lo pendiente de un arranque anterior sale
 *    después, con la hora de **ese** arranque.
 *
 * 3. **La hora del puente es la hora real.** El equipo manda siempre el epoch
 *    actual del puente con el `uptime` del mismo instante, y la antigüedad de
 *    cada trama sale de su `t0Ms`: `UTC = (epoch − uptime) + t0`
 *    (`docs/integracion-ingesta-con-horario.md` §3). Antes el simulador mandaba
 *    `arranque + uptime simulado`, un uptime que avanzaba un lote por envío sin
 *    mirar el reloj: después de unas horas sin usar el chaleco, o de muchos
 *    lotes instantáneos, el epoch quedaba a más de 6 h de la hora del servidor y
 *    el backend contestaba `422 DEVICE_TIME_INVALID`.
 */

import { BOOTID_MODULO, FRAME_BYTES, STEP_MS, readHeader } from './codec/frame'
import type { GeneratorState } from './codec/signal'
import type { BackoffState, DiagAccumulator, PlacementState } from './transmission'
import { EMPTY_DIAG, INITIAL_BACKOFF, INITIAL_PLACEMENT } from './transmission'
import type { Cadence, VestConfig } from './types'

/** Slots de la flash de 16 MB (`FLASH_LOG_FRAME_SLOTS`, `config.h:2276-2286`). */
export const MAX_BACKLOG_FRAMES = 65_504

/** El desborde expulsa por sector de 4 KB: 16 tramas de una vez. */
export const FLASH_SECTOR_FRAMES = 16

/**
 * Tope de tramas por request. Con `duplicatePct` al 100 % el cuerpo se duplica,
 * así que el peor caso son 24.000 × 256 B = 6,1 MB, por debajo del
 * `ingest_max_batch_bytes` de 8 MB del backend.
 */
export const MAX_FRAMES_PER_REQUEST = 12_000

/** Lo que manda el puente real: la ventana en vuelo del equipo (`BRIDGE_LOTE_OBJETIVO`). */
export const BRIDGE_POST_FRAMES = 48

/** Cuánto venía prendido el equipo antes de la primera muestra de un chaleco nuevo. */
export const WARMUP_MS = 30_000

/** Lo que tarda en volver a grabar después de un reinicio. */
export const BOOT_DELAY_MS = 3_000

/**
 * Tolerancia del backend entre el epoch del puente y su hora
 * (`ingest_time_sync_max_skew_seconds`, 6 h por defecto).
 */
export const BACKEND_TIME_SKEW_MS = 6 * 3_600_000

/**
 * Cuánto se deja adelantar el reloj del chaleco. Pasa cuando se manda señal más
 * rápido que el tiempo real: los datos ya alcanzaron la hora actual y siguen.
 * Se corta 10 min antes de la tolerancia del backend para que el simulador lo
 * diga en vez de recibir un 422.
 */
export const MAX_FUTURE_SKEW_MS = BACKEND_TIME_SKEW_MS - 10 * 60_000

/** Batería de 1800 mAh, ~10 días de autonomía. */
const BATTERY_DRAIN_PCT_PER_HOUR = 100 / 240

export interface DeviceClock {
  bootId: number
  /** Próxima `seq` a **grabar**. Solo avanza al generar señal. */
  nextSeq: number
  /**
   * `millis()` de la próxima muestra a grabar, **sin** la vuelta de 32 bits: la
   * trama lo escribe módulo 2³², acá hace falta para ubicar la muestra en la
   * hora de pared.
   */
  t0Ms: number
  /** Hora UTC en que `millis()` valía cero en este arranque. */
  bootEpochMs: number
  /**
   * `bootId → bootEpochMs` de cada arranque visto, como la tabla `porBoot` del
   * puente (`BridgeTimeSync.h:490-547`). Es lo que permite mandar el backlog de
   * un arranque anterior con la hora de ese arranque.
   */
  bootAnchors: Record<string, number>
  batteryPct: number
  /** Estado del generador de señal; `null` hasta el primer lote. */
  genState: GeneratorState | null
  /**
   * Todavía no grabó nada. La primera corrida lo re-ancla para que la señal
   * termine en la hora actual en vez de arrancar en ella.
   */
  fresh: boolean
  backoff: BackoffState
  /** Acumulador de las cabeceras `X-Device-*`, como el `BridgeDiagAcc` del puente. */
  diag: DiagAccumulator
  placement: PlacementState
}

/** Una trama en la flash: grabada, todavía sin confirmar. */
export interface PendingFrame {
  seq: number
  bytes: Uint8Array
  /** Cuántas veces se intentó transmitirla. Lo lee `applyChannel`. */
  attempts: number
}

export interface DeviceStorage {
  /** Grabadas y sin confirmar, contiguas y ordenadas por `seq`. */
  pending: PendingFrame[]
  /**
   * Tramas que se cayeron del frente del buffer por falta de espacio. Es el
   * `STATUS_FLAG_BACKLOG_OVERFLOW` del equipo: pérdida real de señal.
   */
  overflowed: number
}

/** Estado completo de un equipo entre corridas. */
export interface DeviceRuntime {
  clock: DeviceClock
  sd: DeviceStorage
}

/** Registro por chaleco. El `id` es el del `VestConfig`. */
export type ClockRegistry = Map<string, DeviceRuntime>

/** Un equipo nunca usado: anclado a ahora, hasta que la primera corrida lo re-ancle. */
export function initialClock(now = Date.now()): DeviceClock {
  const bootEpochMs = now - WARMUP_MS
  return {
    bootId: 0,
    nextSeq: 0,
    t0Ms: WARMUP_MS,
    bootEpochMs,
    bootAnchors: { 0: bootEpochMs },
    batteryPct: 96,
    genState: null,
    fresh: true,
    backoff: { ...INITIAL_BACKOFF },
    diag: { ...EMPTY_DIAG },
    placement: { ...INITIAL_PLACEMENT },
  }
}

/**
 * El equipo, retomado donde quedó. Devuelve siempre la **misma** instancia para
 * el mismo `id`: quien la tiene la muta en el lugar, así que un `stop` a mitad de
 * corrida deja el cursor y la flash en el último lote efectivamente enviado.
 *
 * `restored` permite sembrar el reloj desde `localStorage`. La flash se hidrata
 * aparte, desde IndexedDB (`flashStore.ts`).
 */
export function acquireDevice(
  registry: ClockRegistry,
  id: string,
  restored?: DeviceClock,
): DeviceRuntime {
  const existing = registry.get(id)
  if (existing) return existing
  const device: DeviceRuntime = {
    clock: restored ?? initialClock(),
    sd: { pending: [], overflowed: 0 },
  }
  registry.set(id, device)
  return device
}

/** Solo al quitar el chaleco de la flota: ese equipo ya no existe. */
export function forgetClock(registry: ClockRegistry, id: string): void {
  registry.delete(id)
}

/** Hora de pared de la próxima muestra a grabar. */
export function dataCursorEpochMs(clock: DeviceClock): number {
  return clock.bootEpochMs + clock.t0Ms
}

/** Factor de la cadencia: cuántas veces más rápido que el tiempo real salen los lotes. */
function cadenceFactor(cadence: Cadence): number {
  switch (cadence.kind) {
    case 'instant':
      return Number.POSITIVE_INFINITY
    case 'accelerated':
      return Math.max(1, cadence.factor)
    case 'realtime':
      return 1
  }
}

/**
 * Cuánto antes de ahora arranca la señal de la primera corrida.
 *
 * Se elige para que **ningún lote quede en el futuro en el momento de
 * enviarse**: el lote `k` sale `k·B/f` después de arrancar y termina `(k+1)·B`
 * después del inicio de la señal. Despejando para el último lote da
 * `B + (N−1)·B·(1 − 1/f)`. En modo instantáneo es la corrida entera
 * (`[ahora − N·B, ahora]`), en tiempo real es un solo lote: el primero termina
 * justo cuando se manda, y los demás a medida que pasa el tiempo.
 */
export function firstRunLeadMs(
  config: Pick<VestConfig, 'batchMinutes' | 'batchCount' | 'cadence'>,
) {
  const batchMs = config.batchMinutes * 60_000
  const f = cadenceFactor(config.cadence)
  return batchMs + (config.batchCount - 1) * batchMs * (1 - 1 / f)
}

/**
 * Ancla la primera corrida de un chaleco nuevo para que su señal termine en la
 * hora actual. No hace nada si el equipo ya grabó: lo que sigue continúa desde
 * el último dato, haya pasado el tiempo que haya pasado.
 */
export function anchorFirstRun(
  clock: DeviceClock,
  config: Pick<VestConfig, 'batchMinutes' | 'batchCount' | 'cadence'>,
  now = Date.now(),
): boolean {
  if (!clock.fresh) return false
  const start = now - firstRunLeadMs(config)
  clock.bootEpochMs = start - WARMUP_MS
  clock.t0Ms = WARMUP_MS
  clock.bootAnchors = { ...clock.bootAnchors, [clock.bootId]: clock.bootEpochMs }
  clock.fresh = false
  return true
}

/** `bootId` escrito en la cabecera de la trama (byte 3, bits 4-7). */
export function frameBootId(frame: Uint8Array): number {
  return (frame[3] & 0xf0) >> 4
}

export type TimeSyncSource = 'ntp' | 'none'

export interface BridgeTime {
  epochMs: number
  uptimeMs: number
  /** El arranque al que pertenece el par epoch/uptime (`X-Device-Boot-Id`). */
  bootId: number
  source: TimeSyncSource
  uncertaintyMs: number
  /** Cuánto se adelantó el epoch respecto de la hora real para cubrir datos futuros. */
  aheadMs: number
}

export interface BridgeTimeFaults {
  /** El puente no consiguió SNTP y tomó la hora del `Date` de `GET /health`. */
  noSntp: boolean
  /** El puente perdió la tabla de arranques: el backlog viejo sale con el par actual. */
  lostBootTable: boolean
}

const NO_FAULTS: BridgeTimeFaults = { noSntp: false, lostBootTable: false }

/** Incertidumbre del SNTP recién sincronizado (`BRIDGE_TIME_FRESH_SYNC_UNCERTAINTY_MS`). */
const NTP_UNCERTAINTY_MS = 200
/** `Date` por HTTP: 1 s de resolución más medio RTT (`BRIDGE_TIME_HTTP_DATE_UNCERTAINTY_MS`). */
const HTTP_DATE_UNCERTAINTY_MS = 1000 + 40

/** `t0Ms` de la trama sin la vuelta de 32 bits, tomando como referencia el cursor. */
function unwrapT0(t0: number, referenceMs: number): number {
  const wrap = 0x1_0000_0000
  if (referenceMs < wrap) return t0
  return t0 + wrap * Math.floor((referenceMs - t0) / wrap)
}

/**
 * Hora que pondría el puente en un POST con estas tramas: el port de
 * `bridgeHoraParaLote()` (`BridgeTimeSync.h:527-547`).
 *
 * - El epoch es la hora actual. Si los datos del POST quedaron adelante de la
 *   hora real (se mandó señal más rápido que el tiempo), se adelanta lo justo
 *   para que el uptime cubra la última muestra; el backend fecha esas muestras
 *   por recepción y las marca como hora no verificada.
 * - El uptime es el del arranque **de las tramas**, y `X-Device-Boot-Id` lo dice:
 *   el backlog de un arranque anterior viaja con la hora de ese arranque.
 */
export function bridgeTimeForPost(
  clock: DeviceClock,
  frames: PendingFrame[],
  now = Date.now(),
  faults: BridgeTimeFaults = NO_FAULTS,
): BridgeTime {
  const source: TimeSyncSource = faults.noSntp ? 'none' : 'ntp'
  const uncertaintyMs = faults.noSntp ? HTTP_DATE_UNCERTAINTY_MS : NTP_UNCERTAINTY_MS
  if (frames.length === 0) return bridgeTimeNow(clock, now, faults)

  const framesBoot = frameBootId(frames[0].bytes)
  const isCurrent = framesBoot === clock.bootId
  const framesAnchor = isCurrent ? clock.bootEpochMs : clock.bootAnchors[framesBoot]
  const last = readHeader(frames[frames.length - 1].bytes)
  const lastT0 = isCurrent ? unwrapT0(last.t0Ms, clock.t0Ms) : last.t0Ms
  const dataEndMs =
    framesAnchor === undefined ? now : framesAnchor + lastT0 + last.durationMs + STEP_MS
  const epochMs = Math.max(now, dataEndMs)

  // Sin la entrada en la tabla, el puente manda el par del arranque actual.
  const useOwnBoot = !isCurrent && framesAnchor !== undefined && !faults.lostBootTable
  const bootId = useOwnBoot ? framesBoot : clock.bootId
  const anchor = useOwnBoot ? framesAnchor : clock.bootEpochMs

  return {
    epochMs,
    uptimeMs: Math.max(0, epochMs - anchor),
    bootId,
    source,
    uncertaintyMs,
    aheadMs: epochMs - now,
  }
}

/** Hora del puente fuera de un lote (`/ingest/device-status`): el par del arranque actual. */
export function bridgeTimeNow(
  clock: DeviceClock,
  now = Date.now(),
  faults: BridgeTimeFaults = NO_FAULTS,
): BridgeTime {
  const epochMs = Math.max(now, dataCursorEpochMs(clock))
  return {
    epochMs,
    uptimeMs: Math.max(0, epochMs - clock.bootEpochMs),
    bootId: clock.bootId,
    source: faults.noSntp ? 'none' : 'ntp',
    uncertaintyMs: faults.noSntp ? HTTP_DATE_UNCERTAINTY_MS : NTP_UNCERTAINTY_MS,
    aheadMs: epochMs - now,
  }
}

/**
 * Reinicio del equipo: `bootId` avanza y el reloj vuelve a cero. La `seq` **no**
 * rebobina: los segmentos del estudio se nombran en S3 con el `first_seq` del
 * lote, y volver a 0 pisaría los ya archivados.
 *
 * **La flash no se toca**: sobrevive al corte de energía. Lo que quedaba sin
 * confirmar sale en POST propios, cortados en el cambio de arranque y con la
 * hora del arranque viejo, que el puente tiene en su tabla.
 */
export function reboot(device: DeviceRuntime): void {
  const clock = device.clock
  const restartAt = dataCursorEpochMs(clock) + BOOT_DELAY_MS
  clock.bootId = (clock.bootId + 1) % BOOTID_MODULO
  clock.t0Ms = 0
  clock.bootEpochMs = restartAt
  clock.bootAnchors = { ...clock.bootAnchors, [clock.bootId]: restartAt }
  clock.fresh = false
}

/**
 * Guarda en la flash las tramas recién grabadas. Devuelve cuántas se perdieron
 * por desborde: se expulsan por sector desde el frente, porque lo más viejo es
 * lo que el equipo ya no puede sostener.
 */
export function recordFrames(sd: DeviceStorage, frames: Uint8Array[], firstSeq: number): number {
  for (let i = 0; i < frames.length; i++) {
    sd.pending.push({ seq: firstSeq + i, bytes: frames[i], attempts: 0 })
  }
  const excess = sd.pending.length - MAX_BACKLOG_FRAMES
  if (excess <= 0) return 0
  const evicted = Math.min(
    sd.pending.length,
    Math.ceil(excess / FLASH_SECTOR_FRAMES) * FLASH_SECTOR_FRAMES,
  )
  sd.pending.splice(0, evicted)
  sd.overflowed += evicted
  return evicted
}

/**
 * Libera la flash hasta la `seq` que el backend confirmó, y solo hasta ahí: lo
 * que quedó después del hueco se retransmite. Devuelve cuántas tramas se
 * liberaron.
 */
export function ackUpTo(sd: DeviceStorage, lastAcceptedSeq: number | null): number {
  if (lastAcceptedSeq === null) return 0
  let freed = 0
  while (freed < sd.pending.length && sd.pending[freed].seq <= lastAcceptedSeq) freed++
  if (freed > 0) sd.pending.splice(0, freed)
  return freed
}

/** Bytes que ocupa el backlog. */
export function backlogBytes(sd: DeviceStorage): number {
  return sd.pending.length * FRAME_BYTES
}

/**
 * Avanza el reloj un lote **grabado**. Corre aunque el envío falle: el firmware
 * sigue grabando en la flash con o sin WiFi.
 */
export function advanceClock(
  clock: DeviceClock,
  batch: { lastSeq: number; sampleCount: number },
): void {
  clock.nextSeq = batch.lastSeq + 1
  clock.t0Ms += batch.sampleCount * STEP_MS
  const hours = (batch.sampleCount * STEP_MS) / 3_600_000
  clock.batteryPct = Math.max(3, clock.batteryPct - hours * BATTERY_DRAIN_PCT_PER_HOUR)
}

/** `batteryFlags` del STATUS: 0x01 medida, 0x02 baja, 0x04 crítica. */
export function batteryFlags(batteryPct: number): number {
  let flags = 0x01
  if (batteryPct <= 15) flags |= 0x02
  if (batteryPct <= 5) flags |= 0x04
  return flags
}
