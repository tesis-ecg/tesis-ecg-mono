/**
 * Reloj, cursor y almacenamiento de un chaleco simulado.
 *
 * Vive fuera de la corrida a propósito. `seq` no es un número de orden
 * decorativo: es la identidad de la trama, y el backend descarta como duplicado
 * todo lo que llegue con un `seq` ya confirmado (`_ack_window` en
 * `ingest_service.py`). Ese dedupe existe porque el firmware libera la SD recién
 * con el ACK: si el ACK se pierde en el camino, el equipo reenvía un lote que el
 * backend ya guardó, y sin dedupe esa hora de señal entraría dos veces en el
 * estudio.
 *
 * De ahí las dos reglas que sostienen este módulo:
 *
 * 1. **El cursor no se reinicia entre corridas.** Un equipo real vuelve a cero
 *    solo cuando se reinicia, y ahí cambia el `bootId`. Empezar de nuevo en
 *    `seq = 0` con el mismo `bootId` es un estado que el hardware no puede
 *    producir, y el backend solo lo puede leer como lo que parece: el mismo lote
 *    otra vez, que se confirma sin guardarse.
 *
 * 2. **Grabar y transmitir son cosas distintas.** El equipo graba en la SD con o
 *    sin WiFi, y una trama se borra de la SD **recién cuando el backend la
 *    confirma** (`INTEGRACION.md` §4.6: ventana deslizante go-back-N, se reenvía
 *    desde la más vieja sin confirmar). Por eso `nextSeq` avanza con la
 *    generación pero el cuerpo del POST sale de `pending`, no del último lote.
 *    Antes esto no era así: el cursor avanzaba las tramas generadas y las que el
 *    backend no había aceptado no se reenviaban nunca, así que un solo hueco
 *    dejaba el estudio congelado para siempre.
 */

import { BOOTID_MODULO, FRAME_BYTES, STEP_MS } from './codec/frame'
import type { VestConfig } from './types'

/**
 * Tope del backlog en tramas. Con ~8.600 tramas por hora de señal son unas 5 h
 * de desconexión antes de empezar a perder, y 12 MB en memoria.
 */
export const MAX_BACKLOG_FRAMES = 48_000

/**
 * Tope de tramas distintas por request. Con `duplicatePct` al 100 % el cuerpo se
 * duplica, así que el peor caso son 24.000 × 256 B = 6,1 MB, por debajo del
 * `ingest_max_batch_bytes` de 8 MB del backend.
 */
export const MAX_FRAMES_PER_REQUEST = 12_000

export interface DeviceClock {
  bootId: number
  /** Próxima `seq` a **grabar**. Solo avanza al generar señal. */
  nextSeq: number
  t0Ms: number
  uptimeMs: number
  batteryPct: number
}

/** Una trama en la SD: grabada, todavía sin confirmar. */
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

export function initialClock(config: VestConfig): DeviceClock {
  return {
    bootId: 0,
    nextSeq: 0,
    t0Ms: 0,
    // Arranca con horas de encendido para que el ancla temporal del backend sea
    // `recepción − uptime`, como en el equipo real.
    uptimeMs: config.batchMinutes * 60_000,
    batteryPct: 96,
  }
}

/**
 * El equipo, retomado donde quedó. Devuelve siempre la **misma** instancia para
 * el mismo `id`: quien la tiene la muta en el lugar, así que un `stop` a mitad de
 * corrida deja el cursor y la SD en el último lote efectivamente enviado.
 *
 * `restored` permite sembrar el reloj desde `localStorage`. La SD nunca se
 * restaura: son megabytes de binario y no entran en el storage del navegador.
 */
export function acquireDevice(
  registry: ClockRegistry,
  id: string,
  config: VestConfig,
  restored?: DeviceClock,
): DeviceRuntime {
  const existing = registry.get(id)
  if (existing) return existing
  const device: DeviceRuntime = {
    clock: restored ?? initialClock(config),
    sd: { pending: [], overflowed: 0 },
  }
  registry.set(id, device)
  return device
}

/** Solo al quitar el chaleco de la flota: ese equipo ya no existe. */
export function forgetClock(registry: ClockRegistry, id: string): void {
  registry.delete(id)
}

/**
 * Reinicio del equipo: `bootId` avanza y el reloj vuelve a cero.
 *
 * El `seq` sigue corriendo. El backend nombra los objetos del estudio en S3 con
 * el `first_seq` del lote (`segment_key`, `envelope_key`), sin el `bootId`, así
 * que rebobinarlo pisaría los segmentos del boot anterior.
 *
 * La SD se vacía. En el equipo real sobreviviría al corte, pero acá las tramas
 * ya tienen el `bootId` viejo escrito en la cabecera y el backend procesa un solo
 * `bootId` por request: reenviarlas mezclaría dos boots bajo una sola ancla
 * temporal. Lo que quedaba sin confirmar cuenta como pérdida del boot anterior,
 * que es exactamente lo que el backend registra como hueco.
 */
export function reboot(device: DeviceRuntime): number {
  const lost = device.sd.pending.length
  device.clock.bootId = (device.clock.bootId + 1) % BOOTID_MODULO
  device.clock.t0Ms = 0
  device.clock.uptimeMs = 0
  device.sd.pending = []
  device.sd.overflowed = 0
  return lost
}

/**
 * Guarda en la SD las tramas recién grabadas. Devuelve cuántas se perdieron por
 * desborde del buffer, que se descartan por el frente: lo más viejo es lo que el
 * equipo ya no puede sostener.
 */
export function recordFrames(sd: DeviceStorage, frames: Uint8Array[], firstSeq: number): number {
  for (let i = 0; i < frames.length; i++) {
    sd.pending.push({ seq: firstSeq + i, bytes: frames[i], attempts: 0 })
  }
  const excess = sd.pending.length - MAX_BACKLOG_FRAMES
  if (excess <= 0) return 0
  sd.pending.splice(0, excess)
  sd.overflowed += excess
  return excess
}

/**
 * Ventana a transmitir: desde la trama más vieja sin confirmar, acotada por lo
 * que entra en un request.
 */
export function takeWindow(sd: DeviceStorage): PendingFrame[] {
  return sd.pending.slice(0, MAX_FRAMES_PER_REQUEST)
}

/**
 * Libera la SD hasta la `seq` que el backend confirmó, y solo hasta ahí: lo que
 * quedó después del hueco se retransmite en el ciclo siguiente. Devuelve cuántas
 * tramas se liberaron.
 */
export function ackUpTo(sd: DeviceStorage, lastAcceptedSeq: number | null): number {
  if (lastAcceptedSeq === null) return 0
  let freed = 0
  while (freed < sd.pending.length && sd.pending[freed].seq <= lastAcceptedSeq) freed++
  if (freed > 0) sd.pending.splice(0, freed)
  return freed
}

/** Bytes que ocupa el backlog. Para mostrarlo en la tarjeta. */
export function backlogBytes(sd: DeviceStorage): number {
  return sd.pending.length * FRAME_BYTES
}

/**
 * Avanza el reloj un lote **grabado**. Corre aunque el envío falle: el firmware
 * sigue grabando en la SD con o sin WiFi. Lo que se transmite lo decide la SD,
 * no este cursor.
 */
export function advanceClock(
  clock: DeviceClock,
  batch: { lastSeq: number; sampleCount: number },
  batchMinutes: number,
): void {
  clock.nextSeq = batch.lastSeq + 1
  clock.t0Ms = (clock.t0Ms + batch.sampleCount * STEP_MS) >>> 0
  clock.uptimeMs += batchMinutes * 60_000
  clock.batteryPct = Math.max(5, clock.batteryPct - 0.4)
}
