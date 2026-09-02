/**
 * Orquesta N chalecos simulados en paralelo.
 *
 * Cada chaleco tiene su propio worker, su propio cursor de `seq`, su propia SD,
 * su propio reloj simulado y su propio estado de red. No comparten nada: eso es
 * lo que permite tener uno mandando backlog acelerado mientras otro falla la
 * autenticación, que es el punto de poder simular una flota.
 *
 * El ciclo por lote es el del equipo real: **grabar, transmitir, confirmar**.
 * Grabar avanza el cursor y llena la SD; transmitir manda la ventana más vieja
 * sin confirmar; confirmar libera de la SD solo lo que el backend aceptó. Lo que
 * quedó del otro lado de un hueco vuelve a salir en el ciclo siguiente
 * (`INTEGRACION.md` §4.6). Antes el cursor avanzaba con lo generado y nada se
 * retransmitía nunca: el primer hueco congelaba el estudio para siempre.
 */

import { useCallback, useEffect, useRef, useState } from 'react'

import { unwrapError } from '@/lib/api'

import {
  postDeviceStatus,
  simulateAnomaly as postSimulatedAnomaly,
  uploadWithRetries,
  type SimulateAnomalyBody,
} from '../api/simulatorApi'
import {
  ackUpTo,
  acquireDevice,
  advanceClock,
  forgetClock,
  reboot,
  recordFrames,
  takeWindow,
  type ClockRegistry,
  type DeviceClock,
} from '../deviceClock'
import { loadClocks, loadFleet, saveClocks, saveFleet } from '../storage'
import type { LogEntry, VestConfig, VestState } from '../types'
import { EMPTY_STATS } from '../types'
import { applyChannel, makeRng } from '../codec/channel'
import {
  buildBatch,
  splitFrames,
  type VestWorkerRequest,
  type VestWorkerResponse,
} from '../codec/batchBuilder'

const MAX_LOG_ENTRIES = 60

/**
 * Ciclos extra de retransmisión al terminar los lotes. Sin esto, lo que se
 * perdió en el último envío queda colgado en la SD y el estudio termina corto
 * justo por la cantidad de señal que el usuario pidió simular.
 */
const MAX_DRAIN_CYCLES = 4

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

/**
 * @param initial configs de arranque. Si no se pasan, la flota se hidrata desde
 * `localStorage` — es lo que hace que la API key sobreviva a un F5.
 */
export function useVestFleet(initial?: VestConfig[]) {
  const [vests, setVests] = useState<VestState[]>(() => (initial ?? loadFleet()).map(toState))
  const controllers = useRef(new Map<string, AbortController>())
  const clocks = useRef<ClockRegistry>(new Map())
  // Relojes de sesiones anteriores. Se consumen al adquirir el equipo por
  // primera vez; la SD no se restaura porque no entra en `localStorage`.
  const restoredClocks = useRef<Record<string, DeviceClock>>(loadClocks())

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
    const snapshot: Record<string, DeviceClock> = {}
    clocks.current.forEach((device, id) => {
      snapshot[id] = { ...device.clock }
    })
    saveClocks(snapshot)
  }, [])

  const patch = useCallback((id: string, update: (state: VestState) => VestState) => {
    setVests((current) => current.map((vest) => (vest.config.id === id ? update(vest) : vest)))
  }, [])

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
      persistClocks()
      setVests((current) => current.filter((vest) => vest.config.id !== id))
    },
    [persistClocks],
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
   * Ciclo de energía del equipo. Es la salida del callejón sin salida que deja
   * un F5 a mitad de corrida: el reloj se restaura pero la SD no, así que el
   * backend queda esperando tramas que ya no existen. Con el `bootId` nuevo el
   * backend acepta desde donde arranque el próximo lote y el estudio vuelve a
   * crecer, con el hueco temporal registrado.
   */
  const rebootVest = useCallback(
    (id: string) => {
      const target = vests.find((vest) => vest.config.id === id)
      if (!target) return
      const device = acquireDevice(clocks.current, id, target.config, restoredClocks.current[id])
      const lost = reboot(device)
      persistClocks()
      patch(id, (vest) => ({
        ...vest,
        stats: {
          ...vest.stats,
          bootId: device.clock.bootId,
          uptimeMs: device.clock.uptimeMs,
          framesPending: 0,
          framesLost: vest.stats.framesLost + lost,
        },
      }))
      log(
        id,
        'warn',
        `Reinicio del equipo: bootId ${device.clock.bootId}, t0Ms vuelve a 0` +
          (lost > 0 ? `, se pierden ${lost} tramas sin confirmar de la SD` : ''),
      )
    },
    [vests, patch, log, persistClocks],
  )

  /**
   * Prende y apaga la colocación del chaleco por el canal corto del equipo.
   *
   * No pasa por el worker ni por el ciclo de lotes a propósito: el equipo real
   * reporta esto **fuera** del ciclo de envío, justamente para que el paciente
   * no se entere una hora después. Por eso también anda con el chaleco
   * detenido, que es como se va a usar para probar.
   *
   * El estado se guarda en la config aunque el POST falle no: si el backend no
   * lo registró, la pantalla no puede decir que sí.
   */
  const setPlacement = useCallback(
    async (id: string, ok: boolean) => {
      const target = vests.find((vest) => vest.config.id === id)
      if (!target) return
      const { config } = target
      if (!config.serial || !config.apiKey) return

      const device = acquireDevice(clocks.current, id, config, restoredClocks.current[id])
      try {
        const ack = await postDeviceStatus(
          ok ? 'signal_recovered' : 'lead_off',
          {
            serial: config.serial,
            apiKey: config.apiKey,
            uptimeMs: device.clock.uptimeMs,
            firmwareVersion: '1.4.2',
            batteryPct: device.clock.batteryPct,
          },
          // Por encima del dT del requerimiento: lo que se está simulando es un
          // electrodo suelto sostenido, no un rebote de medio segundo.
          ok ? 0 : 180,
        )
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
      } catch (error) {
        log(id, 'error', `No se pudo reportar la colocación: ${(error as Error).message}`)
      }
    },
    [vests, updateVest, log],
  )

  /**
   * Fabrica un hallazgo clínico sobre la señal ya subida.
   *
   * Necesita `studyId`, es decir un lote ya ingerido: el hallazgo se ancla
   * dentro de la grabación para que la respuesta del paciente sea ubicable en
   * el gráfico, y sin muestras no hay dónde anclarlo.
   */
  const simulateAnomaly = useCallback(
    async (id: string, body: SimulateAnomalyBody) => {
      const target = vests.find((vest) => vest.config.id === id)
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
    [vests, log],
  )

  const run = useCallback(
    async (id: string) => {
      const target = vests.find((vest) => vest.config.id === id)
      if (!target) return
      const config = target.config

      controllers.current.get(id)?.abort()
      const controller = new AbortController()
      controllers.current.set(id, controller)

      // El chaleco sigue siendo el mismo entre corridas: retoma su reloj donde
      // lo dejó. Se muta en el lugar, así que un `stop` a mitad de camino deja
      // el cursor y la SD en el último lote efectivamente enviado.
      const device = acquireDevice(clocks.current, id, config, restoredClocks.current[id])
      const clock = device.clock

      patch(id, (vest) => ({
        ...vest,
        phase: 'generating',
        // Los contadores son de la corrida; el estado del equipo (cursor, boot,
        // uptime, SD, estudio) no, porque no se reinició nada.
        stats: {
          ...EMPTY_STATS,
          lastSeq: vest.stats.lastSeq,
          bootId: clock.bootId,
          uptimeMs: clock.uptimeMs,
          framesPending: device.sd.pending.length,
          studyId: vest.stats.studyId,
        },
      }))

      let cycle = 0

      /**
       * Un intento de transmisión: ventana más vieja sin confirmar → canal →
       * POST → ACK → liberar la SD. Devuelve cuántas tramas confirmó el backend,
       * que es la única medida de si el estudio creció.
       */
      const transmit = async (
        label: string,
      ): Promise<{ freed: number; irrecoverable: boolean }> => {
        const window = takeWindow(device.sd)
        if (window.length === 0) return { freed: 0, irrecoverable: false }

        const firstPendingSeq = window[0].seq
        const channel = applyChannel(
          window,
          config.frames,
          makeRng(config.signal.seed + cycle * 7919 + firstPendingSeq),
        )
        cycle++

        if (channel.droppedSeqs.length) {
          log(
            id,
            'warn',
            `${channel.droppedSeqs.length} tramas perdidas en el envío ` +
              '(quedan en la SD y se retransmiten en el próximo ciclo)',
          )
        }
        if (channel.corruptedSeqs.length) {
          log(id, 'warn', `${channel.corruptedSeqs.length} tramas con CRC roto`)
        }

        if (channel.body.length === 0) {
          log(id, 'warn', `${label}: no salió ninguna trama al aire, se reintenta`)
          return { freed: 0, irrecoverable: false }
        }

        let body = channel.body
        if (config.network.truncateBodyPct > 0) {
          // Corte a mitad del upload: el cuerpo llega incompleto y, si no cae
          // en un múltiplo de 256, el backend lo rechaza entero.
          const keep = Math.floor((body.length * (100 - config.network.truncateBodyPct)) / 100)
          body = body.slice(0, keep)
          log(id, 'warn', `Cuerpo truncado a ${body.length} B`)
        }

        patch(id, (vest) => ({ ...vest, phase: 'uploading' }))

        const result = await uploadWithRetries(
          body,
          {
            serial: config.network.unknownSerial ? 'HOL-NO-EXISTE' : config.serial,
            apiKey: config.network.invalidApiKey ? 'clave-invalida' : config.apiKey,
            uptimeMs: config.network.omitUptime ? null : clock.uptimeMs,
            firmwareVersion: '1.4.2',
            batteryPct: clock.batteryPct,
          },
          config.network.maxRetries,
          controller.signal,
          (message) => log(id, 'warn', message),
        )

        // La SD se libera **solo** hasta lo que el backend confirmó: lo que
        // quedó del otro lado del hueco vuelve a salir en el ciclo siguiente.
        const freed = result.ack ? ackUpTo(device.sd, result.ack.lastAcceptedSeq) : 0

        patch(id, (vest) => ({
          ...vest,
          stats: {
            ...vest.stats,
            framesSent: vest.stats.framesSent + Math.floor(body.length / 256),
            bytesSent: vest.stats.bytesSent + body.length,
            framesAccepted: vest.stats.framesAccepted + (result.ack?.framesAccepted ?? 0),
            framesRejected: vest.stats.framesRejected + (result.ack?.framesRejected ?? 0),
            framesDuplicate: vest.stats.framesDuplicate + (result.ack?.framesDuplicate ?? 0),
            framesPending: device.sd.pending.length,
            framesLost: device.sd.overflowed,
            lastSeq: result.ack?.lastAcceptedSeq ?? vest.stats.lastSeq,
            studyId: result.ack?.studyId ?? vest.stats.studyId,
            lastStatus: result.status,
            lastError: result.errorMessage,
          },
        }))

        if (!result.ok || !result.ack) {
          log(
            id,
            'error',
            `HTTP ${result.status} ${result.errorCode ?? ''} — ${result.errorMessage}`,
          )
          return {
            freed: 0,
            irrecoverable: result.status >= 400 && result.status < 500,
          }
        }

        const ack = result.ack
        log(
          id,
          'info',
          `${label}: ${ack.framesAccepted} aceptadas, ${ack.framesRejected} rechazadas, ` +
            `${ack.framesDuplicate} duplicadas · ${device.sd.pending.length} tramas en la SD`,
        )

        let irrecoverable = false
        if (freed === 0) {
          const expected = (ack.lastAcceptedSeq ?? -1) + 1
          const oldest = device.sd.pending[0]?.seq
          if (oldest !== undefined && oldest > expected) {
            irrecoverable = true
            // El caso del F5: el reloj se restauró pero la SD no, así que las
            // tramas que llenaban el hueco ya no existen y el backend las va a
            // esperar para siempre.
            log(
              id,
              'error',
              `Hueco irrecuperable: el backend espera seq ${expected} y la SD arranca en ` +
                `${oldest}. Esas tramas se perdieron (probablemente al recargar la página). ` +
                'Reiniciá el equipo para reanudar el estudio desde acá.',
            )
          } else if (ack.framesDuplicate > 0) {
            log(
              id,
              'warn',
              `El estudio no crece: las tramas ya estaban confirmadas para bootId ${clock.bootId}.`,
            )
          } else {
            log(
              id,
              'warn',
              'El estudio no crece con este envío: se retransmite en el próximo ciclo.',
            )
          }
        }

        return { freed, irrecoverable }
      }

      try {
        for (let batch = 0; batch < config.batchCount; batch++) {
          controller.signal.throwIfAborted()

          if (config.frames.rebootAtBatch > 0 && batch + 1 === config.frames.rebootAtBatch) {
            const lost = reboot(device)
            log(
              id,
              'warn',
              `Reinicio del equipo: bootId ${clock.bootId}, t0Ms vuelve a 0` +
                (lost > 0 ? `, se pierden ${lost} tramas sin confirmar de la SD` : ''),
            )
          }

          patch(id, (vest) => ({ ...vest, phase: 'generating' }))
          const firstSeq = clock.nextSeq
          const built = await generateBatch({
            requestId: batch,
            signal: { ...config.signal, durationSec: config.batchMinutes * 60 },
            firstSeq,
            bootId: clock.bootId,
            t0Ms: clock.t0Ms,
            simulated: config.frames.simulated,
          })

          // Grabar: entra en la SD y el cursor avanza, haya o no WiFi.
          const overflowed = recordFrames(device.sd, splitFrames(built.body), firstSeq)
          advanceClock(clock, built, config.batchMinutes)
          persistClocks()

          if (overflowed > 0) {
            log(
              id,
              'error',
              `Backlog desbordado: ${overflowed} tramas se perdieron definitivamente ` +
                '(la SD no da abasto con la desconexión).',
            )
          }

          patch(id, (vest) => ({
            ...vest,
            stats: {
              ...vest.stats,
              batchesSent: vest.stats.batchesSent + 1,
              framesGenerated: vest.stats.framesGenerated + built.framesGenerated,
              uncompressedBytes: vest.stats.uncompressedBytes + built.uncompressedBytes,
              framesPending: device.sd.pending.length,
              framesLost: device.sd.overflowed,
              bootId: clock.bootId,
              uptimeMs: clock.uptimeMs,
            },
          }))

          await transmit(`Lote ${batch + 1}/${config.batchCount}`)
          persistClocks()

          if (batch + 1 < config.batchCount) {
            patch(id, (vest) => ({ ...vest, phase: 'waiting' }))
            await sleep(cadenceDelayMs(config), controller.signal)
          }
        }

        // Drenado: lo que se perdió en el último envío todavía está en la SD y
        // sin esto el estudio quedaría corto justo por esas tramas.
        for (let attempt = 0; attempt < MAX_DRAIN_CYCLES; attempt++) {
          if (device.sd.pending.length === 0) break
          controller.signal.throwIfAborted()
          if (attempt === 0) {
            log(id, 'info', `Retransmitiendo ${device.sd.pending.length} tramas sin confirmar`)
          }
          const outcome = await transmit(`Retransmisión ${attempt + 1}/${MAX_DRAIN_CYCLES}`)
          if (outcome.irrecoverable) break
        }
        persistClocks()

        if (device.sd.pending.length > 0) {
          log(
            id,
            'warn',
            `Quedan ${device.sd.pending.length} tramas sin confirmar en la SD. ` +
              'Volver a enviar las retransmite antes de grabar señal nueva.',
          )
        }
        patch(id, (vest) => ({ ...vest, phase: 'done' }))
      } catch (error) {
        persistClocks()
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
    [vests, patch, log, persistClocks],
  )

  const runAll = useCallback(() => {
    vests.forEach((vest) => void run(vest.config.id))
  }, [vests, run])

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
    stop,
    stopAll,
  }
}
