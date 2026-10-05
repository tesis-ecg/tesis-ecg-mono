/**
 * Orquesta N chalecos simulados en paralelo.
 *
 * Cada chaleco tiene su propio worker, su propio cursor de `seq`, su propia
 * flash, su propio reloj y su propio estado de red. No comparten nada: eso es
 * lo que permite tener uno mandando backlog acelerado mientras otro falla la
 * autenticación, que es el punto de poder simular una flota.
 *
 * El ciclo por lote es el del equipo real: **grabar, transmitir, confirmar**.
 *
 * - Grabar avanza el cursor y llena la flash.
 * - Transmitir es una ventana del puente: POSTs de hasta 48 tramas desde la más
 *   vieja sin confirmar, cortados en cada cambio de arranque, mientras haya
 *   avance (`INTEGRACION.md` §4.6).
 * - Confirmar libera de la flash solo lo que el backend aceptó. Lo que quedó del
 *   otro lado de un hueco vuelve a salir en el POST siguiente.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'

import { unwrapError } from '@/lib/api'

import {
  FIRMWARE_VERSION,
  postDeviceStatus,
  simulateAnomaly as postSimulatedAnomaly,
  uploadWithGrace,
  type IngestHeaders,
  type SimulateAnomalyBody,
  type SimulatedAnomalyType,
  type VestStatusEvent,
} from '../api/simulatorApi'
import { applyChannel, makeRng } from '../codec/channel'
import {
  buildBatch,
  splitFrames,
  type VestWorkerRequest,
  type VestWorkerResponse,
} from '../codec/batchBuilder'
import { FRAME_BYTES } from '../codec/frame'
import {
  EPISODE_META,
  initialGeneratorState,
  type Episode,
  type EpisodeKind,
  type ResolvedEpisode,
} from '../codec/signal'
import { makeEpisode } from '../defaults'
import {
  MAX_FRAMES_PER_REQUEST,
  MAX_FUTURE_SKEW_MS,
  acquireDevice,
  advanceClock,
  anchorFirstRun,
  batteryFlags,
  bridgeTimeForPost,
  bridgeTimeNow,
  dataCursorEpochMs,
  forgetClock,
  reboot,
  recordFrames,
  type BridgeTime,
  type ClockRegistry,
  type DeviceClock,
  type DeviceRuntime,
} from '../deviceClock'
import { defaultFlashStore, type FlashStore } from '../flashStore'
import { loadClocks, loadFleet, saveClocks, saveFleet } from '../storage'
import {
  EMPTY_DIAG,
  STATUS_FLAG_BACKLOG_OVERFLOW,
  STATUS_FLAG_UPLINK_DOWN,
  accumulateDiag,
  applyAck,
  backlogSeconds,
  diagHeaders,
  evaluatePlacement,
  nextPostFrames,
  onWindowFailed,
  onWindowOk,
  responseStoredData,
  type PlacementNotice,
} from '../transmission'
import type { LogEntry, VestConfig, VestState, VestStats } from '../types'
import { EMPTY_STATS } from '../types'

const MAX_LOG_ENTRIES = 80

/**
 * Ventanas extra al terminar los lotes. Sin esto, lo que se perdió en el último
 * envío queda colgado en la flash y el estudio termina corto justo por la
 * cantidad de señal que el usuario pidió simular.
 */
const MAX_DRAIN_CYCLES = 4

/** Respuestas inexplicables seguidas antes de dar la ventana por fallida (`BRIDGE_INEXPLICABLES_AVISAR`). */
const MAX_INEXPLICABLE = 3

function cadenceDelayMs(config: VestConfig): number {
  const batchMs = config.batchMinutes * 60_000
  switch (config.cadence.kind) {
    case 'instant':
      return 0
    case 'accelerated':
      return batchMs / Math.max(1, config.cadence.factor)
    case 'realtime':
      return batchMs
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(signal.reason)
      },
      { once: true },
    )
  })
}

/**
 * Genera el lote en un worker. Si el navegador no soporta módulos en workers
 * (o el entorno de test no los tiene), cae a hacerlo en el hilo principal: es
 * más lento pero funcionalmente idéntico.
 */
async function generateBatch(request: VestWorkerRequest): Promise<VestWorkerResponse> {
  if (typeof Worker === 'undefined') return buildBatch(request)

  const worker = new Worker(new URL('../workers/vestWorker.ts', import.meta.url), {
    type: 'module',
  })
  try {
    return await new Promise<VestWorkerResponse>((resolve, reject) => {
      worker.onmessage = (event: MessageEvent<VestWorkerResponse>) => resolve(event.data)
      worker.onerror = () => reject(new Error('El worker de generación falló.'))
      worker.postMessage(request)
    })
  } finally {
    worker.terminate()
  }
}

function toState(config: VestConfig): VestState {
  return { config, phase: 'idle', stats: { ...EMPTY_STATS }, log: [] }
}

/** Episodios de un lote de la corrida, con los tiempos relativos a su inicio. */
export function resolveEpisodes(episodes: Episode[], batchNumber: number): ResolvedEpisode[] {
  return episodes
    .filter((episode) => episode.batch === batchNumber)
    .map((episode) => ({
      kind: episode.kind,
      startSec: Math.max(0, episode.startSec),
      endSec:
        Math.max(0, episode.startSec) +
        (EPISODE_META[episode.kind].instant ? 0 : Math.max(0, episode.durationSec)),
      value: episode.value,
    }))
}

function formatWhen(epochMs: number): string {
  return new Date(epochMs).toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatHours(ms: number): string {
  return ms >= 3_600_000 ? `${(ms / 3_600_000).toFixed(1)} h` : `${Math.round(ms / 60_000)} min`
}

/** Lo que la tarjeta muestra del equipo, leído del reloj y la flash. */
function deviceStats(device: DeviceRuntime, now: number): Partial<VestStats> {
  const { clock, sd } = device
  return {
    bootId: clock.bootId,
    uptimeMs: Math.max(0, now - clock.bootEpochMs),
    dataCursorEpochMs: clock.fresh ? null : dataCursorEpochMs(clock),
    clockAheadMs: clock.fresh ? 0 : Math.max(0, dataCursorEpochMs(clock) - now),
    backoffWindows: clock.backoff.skipWindows,
    batteryPct: clock.batteryPct,
    framesPending: sd.pending.length,
    framesLost: sd.overflowed,
  }
}

function ingestHeaders(config: VestConfig, clock: DeviceClock, time: BridgeTime): IngestHeaders {
  // El RSSI se mide en cada POST; acá oscila unos dBm alrededor del configurado.
  const rssi = config.network.rssiDbm + Math.round((Math.random() - 0.5) * 6)
  return {
    serial: config.network.unknownSerial ? 'HOL-NO-EXISTE' : config.serial,
    apiKey: config.network.invalidApiKey ? 'clave-invalida' : config.apiKey,
    uptimeMs: config.network.omitUptime ? null : time.uptimeMs,
    bridgeEpochMs: time.epochMs,
    bootId: time.bootId,
    timeSource: time.source,
    timeUncertaintyMs: time.uncertaintyMs,
    firmwareVersion: FIRMWARE_VERSION,
    batteryPct: clock.batteryPct,
    diag: diagHeaders(clock.diag, Math.max(-127, Math.min(0, rssi))),
  }
}

export interface VestFleetOptions {
  /** Dónde vive la flash. Por defecto IndexedDB, con respaldo en memoria. */
  flashStore?: FlashStore
  /** Reloj de pared. Inyectable para los tests. */
  now?: () => number
}

/**
 * @param initial configs de arranque. Si no se pasan, la flota se hidrata desde
 * `localStorage` — es lo que hace que la API key sobreviva a un F5.
 */
export function useVestFleet(initial?: VestConfig[], options: VestFleetOptions = {}) {
  const [vests, setVests] = useState<VestState[]>(() => (initial ?? loadFleet()).map(toState))
  // Las corridas son largas y leen la config en vivo (las inyecciones del panel
  // pueden llegar a mitad de camino).
  const vestsRef = useRef(vests)
  useLayoutEffect(() => {
    vestsRef.current = vests
  }, [vests])
  const controllers = useRef(new Map<string, AbortController>())
  const clocks = useRef<ClockRegistry>(new Map())
  // Relojes de sesiones anteriores. Se consumen al adquirir el equipo por
  // primera vez.
  const restoredClocks = useRef<Record<string, DeviceClock>>(loadClocks())
  const [flashStore] = useState<FlashStore>(() => options.flashStore ?? defaultFlashStore())
  const hydrated = useRef(new Set<string>())
  const [now] = useState<() => number>(() => options.now ?? Date.now)

  // Se persiste ante cualquier cambio de config —agregar, quitar, editar,
  // rotar la key— y no solo al guardar el formulario: el objetivo es que no
  // exista ningún estado alcanzable donde el usuario tenga una credencial en
  // pantalla que se pierda al recargar.
  useEffect(() => {
    saveFleet(vests.map((vest) => vest.config))
  }, [vests])

  useEffect(() => {
    const active = controllers.current
    return () => {
      active.forEach((controller) => controller.abort())
      active.clear()
    }
  }, [])

  const persistClocks = useCallback(() => {
    const snapshot: Record<string, DeviceClock> = { ...restoredClocks.current }
    clocks.current.forEach((device, id) => {
      snapshot[id] = { ...device.clock }
    })
    saveClocks(snapshot)
  }, [])

  const persistFlash = useCallback(
    async (id: string, device: DeviceRuntime) => {
      await flashStore.save(id, device.sd)
    },
    [flashStore],
  )

  /** El equipo, con su flash hidratada desde IndexedDB la primera vez. */
  const getDevice = useCallback(
    async (id: string): Promise<DeviceRuntime> => {
      const device = acquireDevice(clocks.current, id, restoredClocks.current[id])
      if (!hydrated.current.has(id)) {
        hydrated.current.add(id)
        const stored = await flashStore.load(id)
        if (stored && device.sd.pending.length === 0) device.sd = stored
      }
      return device
    },
    [flashStore],
  )

  const patch = useCallback((id: string, update: (state: VestState) => VestState) => {
    setVests((current) => current.map((vest) => (vest.config.id === id ? update(vest) : vest)))
  }, [])

  const patchStats = useCallback(
    (id: string, update: Partial<VestStats> | ((stats: VestStats) => Partial<VestStats>)) => {
      patch(id, (vest) => ({
        ...vest,
        stats: {
          ...vest.stats,
          ...(typeof update === 'function' ? update(vest.stats) : update),
        },
      }))
    },
    [patch],
  )

  const log = useCallback(
    (id: string, level: LogEntry['level'], message: string) => {
      patch(id, (vest) => ({
        ...vest,
        log: [{ at: Date.now(), level, message }, ...vest.log].slice(0, MAX_LOG_ENTRIES),
      }))
    },
    [patch],
  )

  const addVest = useCallback((config: VestConfig) => {
    setVests((current) => [...current, toState(config)])
  }, [])

  const removeVest = useCallback(
    (id: string) => {
      controllers.current.get(id)?.abort()
      controllers.current.delete(id)
      forgetClock(clocks.current, id)
      delete restoredClocks.current[id]
      hydrated.current.delete(id)
      void flashStore.remove(id)
      persistClocks()
      setVests((current) => current.filter((vest) => vest.config.id !== id))
    },
    [persistClocks, flashStore],
  )

  const updateVest = useCallback(
    (id: string, changes: Partial<VestConfig>) => {
      patch(id, (vest) => ({ ...vest, config: { ...vest.config, ...changes } }))
    },
    [patch],
  )

  const stop = useCallback((id: string) => {
    controllers.current.get(id)?.abort()
    controllers.current.delete(id)
  }, [])

  const stopAll = useCallback(() => {
    controllers.current.forEach((controller) => controller.abort())
    controllers.current.clear()
  }, [])

  /**
   * Manda un aviso por el canal corto con la hora del arranque actual. Devuelve
   * el ACK, o `null` si falló (y lo deja en el log).
   */
  const sendStatus = useCallback(
    async (
      id: string,
      config: VestConfig,
      device: DeviceRuntime,
      event: VestStatusEvent,
      durationSeconds: number,
      signal?: AbortSignal,
    ) => {
      const time = bridgeTimeNow(device.clock, now(), config.network)
      try {
        return await postDeviceStatus(
          event,
          ingestHeaders(config, device.clock, time),
          durationSeconds,
          { sqi: device.clock.diag.worstSqi || null },
          signal,
        )
      } catch (error) {
        if (signal?.aborted) throw error
        log(id, 'error', `No se pudo mandar el aviso "${event}": ${(error as Error).message}`)
        return null
      }
    },
    [log, now],
  )

  /**
   * Ciclo de energía del equipo. La flash sobrevive: lo pendiente sale después,
   * en POST propios y con la hora del arranque anterior.
   */
  const rebootVest = useCallback(
    async (id: string) => {
      const device = await getDevice(id)
      reboot(device)
      persistClocks()
      patchStats(id, deviceStats(device, now()))
      const pending = device.sd.pending.length
      log(
        id,
        'warn',
        `Reinicio del equipo: bootId ${device.clock.bootId}, t0Ms vuelve a 0` +
          (pending > 0
            ? `. Quedan ${pending} tramas en la flash: salen con la hora del arranque anterior.`
            : '.'),
      )
    },
    [getDevice, patchStats, log, persistClocks, now],
  )

  /**
   * Prende y apaga la colocación del chaleco por el canal corto del equipo, a
   * mano. El equipo también lo hace solo cuando la señal grabada lo amerita
   * (ver `run`).
   *
   * El estado se guarda en la config solo si el POST salió: si el backend no lo
   * registró, la pantalla no puede decir que sí.
   */
  const setPlacement = useCallback(
    async (id: string, ok: boolean) => {
      const target = vestsRef.current.find((vest) => vest.config.id === id)
      if (!target) return
      const { config } = target
      if (!config.serial || !config.apiKey) return

      const device = await getDevice(id)
      // Por encima del dT del requerimiento: lo que se está simulando es un
      // electrodo suelto sostenido, no un rebote de medio segundo.
      const ack = await sendStatus(
        id,
        config,
        device,
        ok ? 'signal_recovered' : 'lead_off',
        ok ? 0 : 180,
      )
      if (!ack) return
      updateVest(id, { placementOk: ok })
      if (ok) {
        log(id, 'info', 'Chaleco bien colocado: se cerró el episodio, sin aviso al paciente.')
      } else if (ack.notified) {
        log(id, 'warn', `Chaleco mal colocado: aviso enviado (alerta ${ack.alertId}).`)
      } else {
        // `notified: false` con el chaleco mal puesto tiene dos causas y las
        // dos se depuran distinto; decir solo "no se notificó" no alcanza.
        log(
          id,
          'warn',
          'Chaleco mal colocado, pero no se notificó: el equipo no tiene paciente asignado ' +
            'o el aviso cayó dentro del debounce del episodio anterior.',
        )
      }
    },
    [getDevice, sendStatus, updateVest, log],
  )

  /**
   * Fabrica un hallazgo clínico sobre la señal ya subida, del lado del backend.
   * Sirve para probar la notificación al paciente; para que la arritmia esté de
   * verdad en el trazado está `injectAnomaly`.
   */
  const simulateAnomaly = useCallback(
    async (id: string, body: SimulateAnomalyBody) => {
      const target = vestsRef.current.find((vest) => vest.config.id === id)
      const studyId = target?.stats.studyId
      if (!studyId) return

      try {
        const anomaly = await postSimulatedAnomaly(studyId, body)
        log(
          id,
          'warn',
          `Anomalía simulada (${body.eventType}, ${body.severity}) a los ` +
            `${Math.round(anomaly.offsetMs / 1000)} s de grabación. Alerta ${anomaly.alertId}.`,
        )
      } catch (error) {
        log(id, 'error', `No se pudo simular la anomalía: ${unwrapError(error)}`)
      }
    },
    [log],
  )

  /**
   * Encola una arritmia para el **próximo lote** que grabe el chaleco: queda
   * en el ECG, la ve el visor y la mide el backend.
   */
  const injectAnomaly = useCallback(
    (id: string, type: SimulatedAnomalyType) => {
      const target = vestsRef.current.find((vest) => vest.config.id === id)
      if (!target) return
      const kind: EpisodeKind = type
      const batchSec = target.config.batchMinutes * 60
      const meta = EPISODE_META[kind]
      const startSec = Math.round(batchSec / 3)
      const episode = makeEpisode(kind, {
        startSec,
        durationSec: Math.min(meta.defaultDurationSec, Math.max(10, batchSec - startSec)),
      })
      updateVest(id, { pendingInjections: [...target.config.pendingInjections, episode] })
      log(id, 'info', `${meta.label} inyectada: va a estar en el próximo lote grabado.`)
    },
    [updateVest, log],
  )

  const run = useCallback(
    async (id: string) => {
      const target = vestsRef.current.find((vest) => vest.config.id === id)
      if (!target) return
      const config = target.config

      controllers.current.get(id)?.abort()
      const controller = new AbortController()
      controllers.current.set(id, controller)

      // El chaleco sigue siendo el mismo entre corridas: retoma su reloj y su
      // flash donde los dejó. Se mutan en el lugar, así que un `stop` a mitad
      // de camino deja el cursor y la flash en el último lote grabado.
      const device = await getDevice(id)
      const clock = device.clock

      if (anchorFirstRun(clock, config, now())) {
        log(
          id,
          'info',
          `Primer envío del equipo: la señal arranca el ${formatWhen(dataCursorEpochMs(clock))} ` +
            'y termina ahora, como un equipo que ya venía grabando.',
        )
      } else {
        const behind = now() - dataCursorEpochMs(clock)
        if (behind > 60_000) {
          log(
            id,
            'info',
            `La señal continúa desde el último dato grabado (${formatWhen(dataCursorEpochMs(clock))}, ` +
              `hace ${formatHours(behind)}). El puente manda la hora actual.`,
          )
        }
      }

      patch(id, (vest) => ({
        ...vest,
        phase: 'generating',
        // Los contadores son de la corrida; el estado del equipo (cursor, boot,
        // flash, estudio) no, porque no se reinició nada.
        stats: {
          ...EMPTY_STATS,
          lastSeq: vest.stats.lastSeq,
          studyId: vest.stats.studyId,
          ...deviceStats(device, now()),
        },
      }))

      let cycle = 0
      let blocked: string | null = null

      const faults = { noSntp: config.network.noSntp, lostBootTable: config.network.lostBootTable }
      const postLimit = Math.min(MAX_FRAMES_PER_REQUEST, Math.max(1, config.network.postFrames))
      const graceMs = Math.max(0, config.network.graceSeconds) * 1000

      /**
       * Una ventana de envío del puente: POSTs desde la trama más vieja sin
       * confirmar mientras haya avance. Devuelve si falló y si el corte es
       * irrecuperable (un 4xx o un hueco que nadie puede llenar).
       */
      const sendWindow = async (
        label: string,
      ): Promise<{ failed: boolean; irrecoverable: boolean; blocked: boolean }> => {
        if (device.sd.pending.length === 0) {
          // Ventana sin nada para mandar: el latido neutro "estoy encendido".
          await sendStatus(id, config, device, 'alive', 0, controller.signal)
          return { failed: false, irrecoverable: false, blocked: false }
        }

        let posts = 0
        let accepted = 0
        let duplicate = 0
        let rejected = 0
        let dropped = 0
        let corrupted = 0
        let noProgress = 0
        let inexplicable = 0
        let failed = false
        let irrecoverable = false

        while (device.sd.pending.length > 0) {
          controller.signal.throwIfAborted()
          const frames = nextPostFrames(device.sd.pending, postLimit)
          const channel = applyChannel(
            frames,
            config.frames,
            makeRng(config.signal.seed + cycle * 7919 + frames[0].seq),
          )
          cycle++
          dropped += channel.droppedSeqs.length
          corrupted += channel.corruptedSeqs.length
          // Todo se perdió en el aire: las tramas ya cuentan como intentadas y
          // la próxima vuelta salen intactas.
          if (channel.body.length === 0) continue

          let body = channel.body
          if (config.network.truncateBodyPct > 0) {
            // Corte a mitad del upload: si no cae en un múltiplo de 256, el
            // backend rechaza el cuerpo entero.
            const keep = Math.floor((body.length * (100 - config.network.truncateBodyPct)) / 100)
            body = body.slice(0, keep)
          }

          const time = bridgeTimeForPost(clock, frames, now(), faults)
          if (time.aheadMs > MAX_FUTURE_SKEW_MS) {
            blocked =
              `El reloj del chaleco iría ${formatHours(time.aheadMs)} adelantado, más que la ` +
              'tolerancia del backend. Esperá a que la hora real alcance a la señal, o subí ' +
              '`INGEST_TIME_SYNC_MAX_SKEW_SECONDS` en el backend.'
            log(id, 'error', blocked)
            return { failed: true, irrecoverable: true, blocked: true }
          }

          patch(id, (vest) => ({ ...vest, phase: 'uploading' }))
          const result = await uploadWithGrace(
            body,
            ingestHeaders(config, clock, time),
            graceMs,
            controller.signal,
            (message) => log(id, 'warn', message),
          )
          posts++

          if (!result.ok || !result.ack) {
            patchStats(id, (stats) => ({
              postsSent: stats.postsSent + 1,
              bytesSent: stats.bytesSent + body.length,
              lastStatus: result.status,
              lastError: result.errorMessage,
            }))
            log(
              id,
              'error',
              `HTTP ${result.status} ${result.errorCode ?? ''} — ${result.errorMessage}`,
            )
            failed = true
            irrecoverable = result.status >= 300 && result.status < 500
            break
          }

          const ack = result.ack
          const outcome = applyAck(device.sd, frames, ack.lastAcceptedSeq)
          if (responseStoredData(ack)) clock.diag = { ...EMPTY_DIAG }
          accepted += ack.framesAccepted
          duplicate += ack.framesDuplicate
          rejected += ack.framesRejected

          patchStats(id, (stats) => ({
            postsSent: stats.postsSent + 1,
            framesSent: stats.framesSent + Math.floor(body.length / FRAME_BYTES),
            bytesSent: stats.bytesSent + body.length,
            framesAccepted: stats.framesAccepted + ack.framesAccepted,
            framesRejected: stats.framesRejected + ack.framesRejected,
            framesDuplicate: stats.framesDuplicate + ack.framesDuplicate,
            lastSeq: ack.lastAcceptedSeq ?? stats.lastSeq,
            studyId: ack.studyId ?? stats.studyId,
            lastStatus: result.status,
            lastError: null,
            clockAheadMs: time.aheadMs,
            ...deviceStats(device, now()),
          }))

          if (outcome.inexplicable) {
            inexplicable++
            log(
              id,
              'warn',
              `ACK inexplicable: lastAcceptedSeq ${ack.lastAcceptedSeq} confirma tramas que nunca salieron.`,
            )
            if (inexplicable >= MAX_INEXPLICABLE) {
              failed = true
              break
            }
            continue
          }
          inexplicable = 0

          if (outcome.freed > 0) {
            noProgress = 0
            continue
          }

          noProgress++
          const expected = (ack.lastAcceptedSeq ?? -1) + 1
          const oldest = device.sd.pending[0]?.seq
          if (oldest !== undefined && ack.lastAcceptedSeq !== null && oldest > expected) {
            // Las tramas que llenaban el hueco ya no están en la flash (se
            // borró el almacenamiento del navegador, o se desbordó).
            irrecoverable = true
            log(
              id,
              'error',
              `Hueco irrecuperable: el backend espera seq ${expected} y la flash arranca en ` +
                `${oldest}. Esas tramas ya no existen. Reiniciá el equipo para reanudar el ` +
                'estudio desde acá.',
            )
            break
          }
          if (noProgress >= 2) break
        }

        const parts = [`${posts} POST`, `${accepted} aceptadas`]
        if (duplicate) parts.push(`${duplicate} duplicadas`)
        if (rejected) parts.push(`${rejected} rechazadas`)
        if (dropped) parts.push(`${dropped} perdidas en el aire (se retransmiten)`)
        if (corrupted) parts.push(`${corrupted} con CRC roto`)
        log(
          id,
          failed ? 'error' : dropped || corrupted ? 'warn' : 'info',
          `${label}: ${parts.join(', ')} · ${device.sd.pending.length} tramas en la flash`,
        )

        if (failed) {
          clock.backoff = onWindowFailed(
            clock.backoff,
            device.sd.pending.length,
            config.batchMinutes,
          )
          clock.diag = accumulateDiag(clock.diag, { statusFlags: STATUS_FLAG_UPLINK_DOWN })
          if (clock.backoff.skipWindows > 0) {
            log(
              id,
              'warn',
              `Ventana fallida (${clock.backoff.failures} seguidas): la próxima se saltea ` +
                `${clock.backoff.skipWindows} lote(s), como el backoff del equipo.`,
            )
          }
        } else {
          clock.backoff = onWindowOk()
        }
        patchStats(id, deviceStats(device, now()))
        return { failed, irrecoverable, blocked: false }
      }

      /** Manda los avisos de colocación que decidió el equipo, antes de las tramas. */
      const sendPlacementNotices = async (notices: PlacementNotice[]) => {
        for (const notice of notices) {
          const ack = await sendStatus(
            id,
            config,
            device,
            notice.event,
            notice.durationSeconds,
            controller.signal,
          )
          if (!ack) continue
          const ok = notice.event === 'signal_recovered'
          updateVest(id, { placementOk: ok })
          log(
            id,
            ok ? 'info' : 'warn',
            ok
              ? `El equipo detectó la señal recuperada (${notice.durationSeconds} s buenos): cerró el aviso.`
              : `El equipo detectó ${notice.event === 'lead_off' ? 'el electrodo suelto' : 'la calidad mala'} ` +
                  `por ${notice.durationSeconds} s y mandó el aviso` +
                  (ack.notified
                    ? ` (alerta ${ack.alertId}).`
                    : ', sin notificar (debounce o sin paciente).'),
          )
        }
      }

      try {
        for (let batch = 0; batch < config.batchCount; batch++) {
          controller.signal.throwIfAborted()

          if (config.frames.rebootAtBatch > 0 && batch + 1 === config.frames.rebootAtBatch) {
            reboot(device)
            log(
              id,
              'warn',
              `Reinicio del equipo: bootId ${clock.bootId}, t0Ms vuelve a 0. ` +
                `${device.sd.pending.length} tramas siguen en la flash.`,
            )
          }

          const batchMs = config.batchMinutes * 60_000
          const ahead = dataCursorEpochMs(clock) + batchMs - now()
          if (ahead > MAX_FUTURE_SKEW_MS) {
            blocked =
              `La señal ya va ${formatHours(Math.max(0, dataCursorEpochMs(clock) - now()))} ` +
              'adelantada a la hora real: otro lote pasaría la tolerancia de hora del backend ' +
              '(6 h). Esperá a que la hora real la alcance o subí ' +
              '`INGEST_TIME_SYNC_MAX_SKEW_SECONDS`.'
            log(id, 'error', blocked)
            break
          }

          // Inyecciones del panel: se leen en vivo, pueden haber llegado
          // durante la corrida.
          const live = vestsRef.current.find((vest) => vest.config.id === id)?.config
          const injections = live?.pendingInjections ?? []
          if (injections.length) updateVest(id, { pendingInjections: [] })
          const episodes = [
            ...resolveEpisodes(config.episodes, batch + 1),
            ...resolveEpisodes(
              injections.map((episode) => ({ ...episode, batch: 1 })),
              1,
            ),
          ]

          patch(id, (vest) => ({ ...vest, phase: 'generating' }))
          const firstSeq = clock.nextSeq
          const startEpochMs = dataCursorEpochMs(clock)
          const built = await generateBatch({
            requestId: batch,
            profile: config.signal,
            durationSec: config.batchMinutes * 60,
            episodes,
            genState: clock.genState ?? initialGeneratorState(config.signal),
            firstSeq,
            bootId: clock.bootId,
            t0Ms: clock.t0Ms,
            wallStartEpochMs: startEpochMs,
            simulated: config.frames.simulated,
          })

          // Grabar: entra en la flash y el cursor avanza, haya o no WiFi.
          clock.genState = built.genState
          const overflowed = recordFrames(device.sd, splitFrames(built.body), firstSeq)
          advanceClock(clock, built)
          const framesPerSecond = built.framesGenerated / (config.batchMinutes * 60)
          clock.diag = accumulateDiag(clock.diag, {
            leadFlags: built.leadFlags,
            worstSqi: built.worstSqi,
            statusFlags: overflowed > 0 ? STATUS_FLAG_BACKLOG_OVERFLOW : 0,
            batteryFlags: batteryFlags(clock.batteryPct),
            backlogSeconds: backlogSeconds(device.sd.pending.length, framesPerSecond),
          })
          const placement = evaluatePlacement(clock.placement, built.secondStatus, startEpochMs)
          clock.placement = placement.state
          persistClocks()
          await persistFlash(id, device)

          if (overflowed > 0) {
            log(
              id,
              'error',
              `Flash desbordada: ${overflowed} tramas se perdieron definitivamente ` +
                '(la desconexión duró más de lo que entra en 16 MB).',
            )
          }
          if (episodes.length) {
            log(
              id,
              'info',
              `Lote ${batch + 1}: ${episodes.map((e) => EPISODE_META[e.kind].label).join(', ')}.`,
            )
          }

          patchStats(id, (stats) => ({
            batchesSent: stats.batchesSent + 1,
            framesGenerated: stats.framesGenerated + built.framesGenerated,
            uncompressedBytes: stats.uncompressedBytes + built.uncompressedBytes,
            ...deviceStats(device, now()),
          }))

          if (clock.backoff.skipWindows > 0) {
            clock.backoff = { ...clock.backoff, skipWindows: clock.backoff.skipWindows - 1 }
            log(
              id,
              'warn',
              `Lote ${batch + 1}: ventana pospuesta por backoff; la señal queda en la flash.`,
            )
          } else {
            await sendPlacementNotices(placement.notices)
            const outcome = await sendWindow(`Lote ${batch + 1}/${config.batchCount}`)
            if (outcome.blocked) break
          }
          persistClocks()
          await persistFlash(id, device)

          if (batch + 1 < config.batchCount) {
            patch(id, (vest) => ({ ...vest, phase: 'waiting' }))
            await sleep(cadenceDelayMs(config), controller.signal)
          }
        }

        // Drenado: lo que se perdió en el último envío todavía está en la flash
        // y sin esto el estudio quedaría corto justo por esas tramas.
        for (let attempt = 0; attempt < MAX_DRAIN_CYCLES && !blocked; attempt++) {
          if (device.sd.pending.length === 0) break
          controller.signal.throwIfAborted()
          const outcome = await sendWindow(`Retransmisión ${attempt + 1}/${MAX_DRAIN_CYCLES}`)
          if (outcome.irrecoverable) break
        }
        persistClocks()
        await persistFlash(id, device)

        if (device.sd.pending.length > 0) {
          log(
            id,
            'warn',
            `Quedan ${device.sd.pending.length} tramas sin confirmar en la flash. ` +
              'Volver a enviar las retransmite antes que la señal nueva.',
          )
        }
        const stoppedBy = blocked
        patch(id, (vest) => ({
          ...vest,
          phase: stoppedBy ? 'error' : 'done',
          stats: {
            ...vest.stats,
            ...deviceStats(device, now()),
            ...(stoppedBy ? { lastError: stoppedBy, lastStatus: null } : {}),
          },
        }))
      } catch (error) {
        persistClocks()
        await persistFlash(id, device)
        if (controller.signal.aborted) {
          patch(id, (vest) => ({ ...vest, phase: 'idle' }))
          log(id, 'info', 'Detenido')
          return
        }
        patch(id, (vest) => ({
          ...vest,
          phase: 'error',
          stats: { ...vest.stats, lastError: (error as Error).message },
        }))
        log(id, 'error', (error as Error).message)
      } finally {
        controllers.current.delete(id)
      }
    },
    [getDevice, patch, patchStats, log, persistClocks, persistFlash, sendStatus, updateVest, now],
  )

  const runAll = useCallback(() => {
    vestsRef.current.forEach((vest) => void run(vest.config.id))
  }, [run])

  return {
    vests,
    addVest,
    removeVest,
    updateVest,
    run,
    runAll,
    rebootVest,
    setPlacement,
    simulateAnomaly,
    injectAnomaly,
    stop,
    stopAll,
  }
}
