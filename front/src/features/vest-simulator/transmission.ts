/**
 * Lo que hace el puente WiFi con la flash del equipo: armar los POST, leer el
 * ACK, acumular el diagnóstico, decidir el backoff y el aviso de colocación.
 *
 * Todo es lógica pura, port de `../Holter-ECG-System`:
 * `BridgeQueue.h`, `BridgeUplink.h`, `HolterLink.h` y `config.h`. El hook
 * (`useVestFleet.ts`) solo la orquesta contra la red.
 */

import type { DeviceStorage, PendingFrame } from './deviceClock'

// --------------------------------------------------------------------------- //
// Armado de POSTs

/** `bootId` de la cabecera de la trama (byte 3, bits 4-7). */
function bootOf(frame: PendingFrame): number {
  return (frame.bytes[3] & 0xf0) >> 4
}

/**
 * Las tramas del próximo POST: desde la más vieja sin confirmar, hasta
 * `maxFrames`, y **cortadas en la primera de otro arranque**
 * (`tramasDelProximoPost`, `BridgeQueue.h:190-198`). Un POST nunca mezcla
 * arranques porque el par epoch/uptime que lo acompaña es de uno solo.
 */
export function nextPostFrames(pending: PendingFrame[], maxFrames: number): PendingFrame[] {
  if (pending.length === 0) return []
  const boot = bootOf(pending[0])
  const limit = Math.min(pending.length, Math.max(1, maxFrames))
  let end = 1
  while (end < limit && bootOf(pending[end]) === boot) end++
  return pending.slice(0, end)
}

// --------------------------------------------------------------------------- //
// ACK

/** Un ACK que adelanta más que esto respecto de lo enviado no se cree (`HolterLink.h`). */
export const MAX_ACK_LOOKAHEAD_FRAMES = 512

export interface AckOutcome {
  freed: number
  /** El ACK confirmaba tramas que nunca salieron: se ignora entero. */
  inexplicable: boolean
}

/**
 * Libera la flash según el `lastAcceptedSeq` de un POST, con las reglas de
 * `HolterBridgeAck::aplicarRespuesta()` (`HolterLink.h:1590-1616`): un ACK que
 * se va más de 512 tramas por delante de lo enviado se descarta, y uno que se
 * pasa del final de lo enviado se recorta ahí.
 */
export function applyAck(
  sd: DeviceStorage,
  sent: PendingFrame[],
  lastAcceptedSeq: number | null,
): AckOutcome {
  if (lastAcceptedSeq === null || sent.length === 0) return { freed: 0, inexplicable: false }
  const firstSent = sent[0].seq
  const end = firstSent + sent.length
  let ackNext = lastAcceptedSeq + 1
  if (ackNext - end > MAX_ACK_LOOKAHEAD_FRAMES) return { freed: 0, inexplicable: true }
  if (ackNext > end) ackNext = end
  let freed = 0
  while (freed < sd.pending.length && sd.pending[freed].seq < ackNext) freed++
  if (freed > 0) sd.pending.splice(0, freed)
  return { freed, inexplicable: false }
}

// --------------------------------------------------------------------------- //
// Diagnóstico `X-Device-*`

/** `STATUS_FLAG_BACKLOG_OVERFLOW`: se pisó backlog no confirmado. */
export const STATUS_FLAG_BACKLOG_OVERFLOW = 0x01
/** `STATUS_FLAG_UPLINK_DOWN`: la última ventana de envío falló. */
export const STATUS_FLAG_UPLINK_DOWN = 0x80

/** El `BridgeDiagAcc` del puente: OR de lo visto desde el último POST guardado. */
export interface DiagAccumulator {
  hasData: boolean
  leadFlags: number
  lossFlags: number
  statusFlags: number
  /** Máximo de segundos de backlog visto. */
  backlogSeconds: number
  /** Peor SQI visto, 1..3; 0 si ninguno. */
  worstSqi: number
  batteryFlags: number
}

export const EMPTY_DIAG: DiagAccumulator = {
  hasData: false,
  leadFlags: 0,
  lossFlags: 0,
  statusFlags: 0,
  backlogSeconds: 0,
  worstSqi: 0,
  batteryFlags: 0,
}

export function accumulateDiag(
  diag: DiagAccumulator,
  update: Partial<Omit<DiagAccumulator, 'hasData'>>,
): DiagAccumulator {
  const sqi = update.worstSqi ?? 0
  return {
    hasData: true,
    leadFlags: diag.leadFlags | (update.leadFlags ?? 0),
    lossFlags: diag.lossFlags | (update.lossFlags ?? 0),
    statusFlags: diag.statusFlags | (update.statusFlags ?? 0),
    backlogSeconds: Math.min(65_535, Math.max(diag.backlogSeconds, update.backlogSeconds ?? 0)),
    worstSqi: sqi === 0 ? diag.worstSqi : diag.worstSqi === 0 ? sqi : Math.min(diag.worstSqi, sqi),
    batteryFlags: diag.batteryFlags | (update.batteryFlags ?? 0),
  }
}

/**
 * Las cabeceras `X-Device-*` de un POST (`addIngestHeaders`,
 * `esp32_wifi_bridge.cpp:1013-1143`): las de flags solo si hay algo acumulado,
 * el SQI solo si se conoce y la batería solo si está medida.
 */
export function diagHeaders(diag: DiagAccumulator, rssiDbm: number | null): Record<string, string> {
  const headers: Record<string, string> = {}
  if (diag.hasData) {
    headers['X-Device-Lead-Flags'] = String(diag.leadFlags & 0xff)
    headers['X-Device-Loss-Flags'] = String(diag.lossFlags & 0xff)
    headers['X-Device-Status-Flags'] = String(diag.statusFlags & 0xff)
    headers['X-Device-Backlog-Seconds'] = String(Math.round(diag.backlogSeconds))
  }
  if (diag.worstSqi >= 1 && diag.worstSqi <= 3) headers['X-Device-Sqi'] = String(diag.worstSqi)
  if (rssiDbm !== null) headers['X-Device-Rssi'] = String(Math.round(rssiDbm))
  if (diag.batteryFlags & 0x01) headers['X-Device-Battery-Flags'] = String(diag.batteryFlags & 0x0f)
  return headers
}

/**
 * Si la respuesta guardó señal nueva. Solo ahí el puente limpia el acumulador
 * (`bridgeRespuestaGuardoLote`): un 202 todo duplicado lo conserva, así el
 * próximo POST vuelve a contar lo mismo.
 */
export function responseStoredData(ack: { framesAccepted: number; framesDuplicate: number }) {
  return ack.framesAccepted > ack.framesDuplicate
}

/** Segundos de señal que representa el backlog, al ritmo de tramas del último lote. */
export function backlogSeconds(pendingFrames: number, framesPerSecond: number): number {
  if (framesPerSecond <= 0) return 0
  return Math.min(65_535, Math.round(pendingFrames / framesPerSecond))
}

// --------------------------------------------------------------------------- //
// Backoff de ventanas

export interface BackoffState {
  /** Ventanas fallidas seguidas. */
  failures: number
  /** Ventanas que quedan por saltear antes de volver a intentar. */
  skipWindows: number
}

export const INITIAL_BACKOFF: BackoffState = { failures: 0, skipWindows: 0 }

/** Intervalo de ventanas del equipo (`HOLTER_LINK_BATCH_INTERVAL_MS`). */
export const WINDOW_INTERVAL_MIN = 10
/** `BACKOFF_MAX_SHIFT`: 10 → 20 → 40 min. */
const BACKOFF_MAX_SHIFT = 2
/** Con la flash al 90 % no hay backoff: el log no puede esperar. */
export const NO_BACKOFF_FROM_FRAMES = Math.floor(65_504 / 10) * 9

/**
 * Una ventana falló. La próxima se abre a 10, 20 o 40 min según cuántas fallaron
 * seguidas; en lotes, eso son las que hay que saltear.
 */
export function onWindowFailed(
  backoff: BackoffState,
  pendingFrames: number,
  batchMinutes: number,
): BackoffState {
  const failures = backoff.failures + 1
  if (pendingFrames >= NO_BACKOFF_FROM_FRAMES) return { failures, skipWindows: 0 }
  const intervalMin = WINDOW_INTERVAL_MIN * 2 ** Math.min(failures - 1, BACKOFF_MAX_SHIFT)
  return {
    failures,
    skipWindows: Math.max(0, Math.ceil(intervalMin / Math.max(1e-6, batchMinutes)) - 1),
  }
}

export function onWindowOk(): BackoffState {
  return { ...INITIAL_BACKOFF }
}

// --------------------------------------------------------------------------- //
// Aviso de colocación

/** Lead-off o calidad mala sostenidos este tiempo disparan el aviso (`config.h:3043`). */
export const PLACEMENT_BAD_MS = 120_000
/** Señal buena sostenida este tiempo cierra el episodio. */
export const PLACEMENT_RECOVER_MS = 60_000
/** Un aviso cada 5 min como mucho (`AVISO_VENTANA_MIN_SEPARACION_MS`). */
export const PLACEMENT_MIN_GAP_MS = 300_000

export type PlacementEvent = 'lead_off' | 'signal_quality_bad' | 'signal_recovered'

export interface PlacementState {
  /** Desde cuándo (hora de la señal) dura la condición mala, o `null` si está bien. */
  badSinceMs: number | null
  /** Desde cuándo está bien después de un aviso, o `null`. */
  goodSinceMs: number | null
  /** Último aviso de problema mandado y todavía sin cerrar. */
  reported: 'lead_off' | 'signal_quality_bad' | null
  lastNoticeMs: number | null
}

export const INITIAL_PLACEMENT: PlacementState = {
  badSinceMs: null,
  goodSinceMs: null,
  reported: null,
  lastNoticeMs: null,
}

export interface PlacementNotice {
  event: PlacementEvent
  durationSeconds: number
  /** Hora de la señal en que se decidió. */
  atMs: number
}

/**
 * Recorre el estado por segundo de un lote y devuelve los avisos que mandaría
 * el equipo. El estado se arrastra entre lotes: un electrodo que se suelta a
 * los 9:30 de un lote de 10 min avisa en el siguiente.
 *
 * @param secondStatus 0 bien, 1 electrodo suelto, 2 calidad mala.
 */
export function evaluatePlacement(
  state: PlacementState,
  secondStatus: Uint8Array,
  startEpochMs: number,
): { state: PlacementState; notices: PlacementNotice[] } {
  const next = { ...state }
  const notices: PlacementNotice[] = []
  for (let s = 0; s < secondStatus.length; s++) {
    const at = startEpochMs + s * 1000
    const status = secondStatus[s]
    if (status !== 0) {
      next.goodSinceMs = null
      next.badSinceMs ??= at
      const lasted = at + 1000 - next.badSinceMs
      const spaced = next.lastNoticeMs === null || at - next.lastNoticeMs >= PLACEMENT_MIN_GAP_MS
      if (next.reported === null && lasted >= PLACEMENT_BAD_MS && spaced) {
        const event = status === 1 ? 'lead_off' : 'signal_quality_bad'
        notices.push({ event, durationSeconds: Math.round(lasted / 1000), atMs: at })
        next.reported = event
        next.lastNoticeMs = at
      }
    } else {
      next.badSinceMs = null
      if (next.reported === null) continue
      next.goodSinceMs ??= at
      const lasted = at + 1000 - next.goodSinceMs
      if (lasted >= PLACEMENT_RECOVER_MS) {
        notices.push({
          event: 'signal_recovered',
          durationSeconds: Math.round(lasted / 1000),
          atMs: at,
        })
        next.reported = null
        next.goodSinceMs = null
      }
    }
  }
  return { state: next, notices }
}
