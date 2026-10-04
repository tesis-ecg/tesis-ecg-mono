/**
 * El ciclo completo del chaleco contra un backend de mentira que reproduce
 * `_ack_window` de `ingest_service.py`.
 *
 * Es la prueba de regresión del bug que motivó todo esto: el simulador avanzaba
 * su cursor las tramas **generadas** e ignoraba `lastAcceptedSeq`, así que la
 * primera trama perdida dejaba al backend esperándola para siempre y el estudio
 * se congelaba con un puñado de muestras. Diez lotes de una hora daban un
 * segundo de señal.
 *
 * El entorno de test es `node` y no hay DOM, así que el ciclo del hook
 * —grabar, transmitir de a POSTs de 48, confirmar— se reproduce acá sobre los
 * mismos módulos que usa `useVestFleet`.
 */

import { describe, expect, it } from 'vitest'

import {
  BRIDGE_POST_FRAMES,
  advanceClock,
  dataCursorEpochMs,
  initialClock,
  reboot,
  recordFrames,
  type DeviceRuntime,
} from './deviceClock'
import { applyChannel, makeRng } from './codec/channel'
import { buildBatch, splitFrames } from './codec/batchBuilder'
import { FRAME_BYTES, FRAME_MAGIC, frameCrc, readHeader } from './codec/frame'
import { batchRequest } from './codec/testSignals'
import { applyAck, nextPostFrames } from './transmission'
import type { FrameAnomalies } from './types'

const CLEAN: FrameAnomalies = {
  corruptCrcPct: 0,
  duplicatePct: 0,
  dropPct: 0,
  rebootAtBatch: 0,
  simulated: true,
  shuffle: false,
}

interface Ack {
  framesAccepted: number
  framesDuplicate: number
  framesRejected: number
  lastAcceptedSeq: number | null
}

/** Puerto del `_ack_window` del backend, con las mismas reglas. */
class FakeIngest {
  lastIngestedSeq: number | null = null
  lastBootId: number | null = null
  /** `seq` archivadas: es el estudio. */
  readonly stored = new Set<number>()

  post(body: Uint8Array): Ack {
    const headers = []
    let rejected = 0
    for (let i = 0; i < body.length; i += FRAME_BYTES) {
      const raw = body.subarray(i, i + FRAME_BYTES)
      const header = readHeader(raw)
      if (header.magic !== FRAME_MAGIC || frameCrc(raw) !== header.crc32) {
        rejected++
        continue
      }
      headers.push(header)
    }
    if (headers.length === 0) throw new Error('INGEST_NO_VALID_FRAMES')

    // El filtro por bootId va antes de deduplicar, igual que en el backend.
    const bootId = headers[0].bootId
    const seqs = [...new Set(headers.filter((h) => h.bootId === bootId).map((h) => h.seq))].sort(
      (a, b) => a - b,
    )

    const cursor = this.lastIngestedSeq
    const rebooted = this.lastBootId !== null && this.lastBootId !== bootId
    const already = cursor === null ? [] : seqs.filter((seq) => seq <= cursor)
    const fresh = cursor === null ? seqs : seqs.filter((seq) => seq > cursor)
    let expected =
      cursor === null ? (fresh[0] ?? 0) : rebooted && fresh.length ? fresh[0] : cursor + 1

    const accepted: number[] = []
    for (const seq of fresh) {
      if (seq !== expected) break
      accepted.push(seq)
      expected++
    }

    for (const seq of accepted) this.stored.add(seq)
    if (accepted.length) {
      this.lastIngestedSeq = accepted[accepted.length - 1]
      this.lastBootId = bootId
    }

    return {
      framesAccepted: already.length + accepted.length,
      framesDuplicate: already.length,
      framesRejected: rejected,
      lastAcceptedSeq: accepted.length ? accepted[accepted.length - 1] : this.lastIngestedSeq,
    }
  }

  /** El estudio "crece" solo si las seq archivadas son contiguas desde la primera. */
  get contiguousFrom(): number | null {
    if (this.stored.size === 0) return null
    return Math.min(...this.stored)
  }
}

function freshDevice(): DeviceRuntime {
  return {
    clock: { ...initialClock(Date.UTC(2026, 9, 3, 12)), fresh: false },
    sd: { pending: [], overflowed: 0 },
  }
}

const DURATION_SEC = 20

/** Graba un lote en la flash y avanza el reloj, como hace el hook. */
function record(device: DeviceRuntime): number {
  const { clock } = device
  const firstSeq = clock.nextSeq
  const built = buildBatch(
    batchRequest({
      durationSec: DURATION_SEC,
      firstSeq,
      bootId: clock.bootId,
      t0Ms: clock.t0Ms,
      wallStartEpochMs: dataCursorEpochMs(clock),
      ...(clock.genState ? { genState: clock.genState } : {}),
    }),
  )
  clock.genState = built.genState
  recordFrames(device.sd, splitFrames(built.body), firstSeq)
  advanceClock(clock, built)
  return built.framesGenerated
}

/**
 * Una ventana del puente: POSTs de 48 desde la más vieja sin confirmar, mientras
 * haya avance. Devuelve las seq que salieron al aire y cuántas se liberaron.
 */
function transmit(
  device: DeviceRuntime,
  backend: FakeIngest,
  anomalies: Partial<FrameAnomalies>,
  cycle: number,
): { sentSeqs: number[]; freed: number } {
  const sentSeqs: number[] = []
  let freed = 0
  let noProgress = 0
  let post = 0
  while (device.sd.pending.length > 0 && noProgress < 2) {
    const frames = nextPostFrames(device.sd.pending, BRIDGE_POST_FRAMES)
    const channel = applyChannel(
      frames,
      { ...CLEAN, ...anomalies },
      makeRng(1234 + (cycle * 1000 + post++) * 7919 + frames[0].seq),
    )
    sentSeqs.push(
      ...frames.map((frame) => frame.seq).filter((seq) => !channel.droppedSeqs.includes(seq)),
    )
    if (channel.body.length === 0) continue

    const ack = backend.post(channel.body)
    const outcome = applyAck(device.sd, frames, ack.lastAcceptedSeq)
    freed += outcome.freed
    noProgress = outcome.freed > 0 ? 0 : noProgress + 1
  }
  return { sentSeqs, freed }
}

/**
 * Lo que tiene que valer al terminar una corrida: la flash vacía y el estudio
 * contiguo hasta la última trama grabada.
 *
 * No se exige que el estudio arranque en la seq 0. En el primer envío de un
 * estudio nuevo el backend todavía no tiene cursor, así que lo fija en la
 * primera trama que le llega: lo que se haya perdido **antes** de esa no se
 * puede reclamar, porque nadie sabe que existió. Es pérdida real y acotada al
 * arranque. Lo que sí es un bug —y era el bug— es que falte algo del final.
 */
function expectCompleteStudy(backend: FakeIngest, device: DeviceRuntime, generated: number) {
  const seqs = [...backend.stored].sort((a, b) => a - b)

  expect(device.sd.pending).toHaveLength(0)
  expect(seqs[seqs.length - 1]).toBe(generated - 1)
  expect(seqs).toEqual(seqs.map((_, i) => seqs[0] + i))
  expect(seqs[0]).toBeLessThan(5)
}

function runVest(batches: number, anomalies: Partial<FrameAnomalies> = {}) {
  const device = freshDevice()
  const backend = new FakeIngest()
  let cycle = 0
  let generated = 0

  for (let batch = 0; batch < batches; batch++) {
    generated += record(device)
    transmit(device, backend, anomalies, cycle++)
  }
  // Drenado, igual que el hook al terminar los lotes.
  for (let attempt = 0; attempt < 4 && device.sd.pending.length > 0; attempt++) {
    if (transmit(device, backend, anomalies, cycle++).freed === 0) break
  }

  return { device, backend, generated }
}

describe('go-back-N contra el backend', () => {
  it('un canal limpio archiva todo lo grabado y deja la flash vacía', () => {
    const { backend, device, generated } = runVest(3)

    expect(backend.stored.size).toBe(generated)
    expect(device.sd.pending).toHaveLength(0)
  })

  it('con pérdidas, el estudio termina completo igual: nada se queda afuera', () => {
    // El caso del reporte: 30 % de tramas perdidas. Antes el estudio se
    // congelaba en las primeras muestras porque las perdidas no volvían a
    // salir nunca.
    const { backend, device, generated } = runVest(3, { dropPct: 30 })

    expectCompleteStudy(backend, device, generated)
  })

  it('la trama perdida en el aire vuelve a salir en el POST siguiente', () => {
    const device = freshDevice()
    const backend = new FakeIngest()

    const generated = record(device)
    const window = transmit(device, backend, { dropPct: 40 }, 0)

    // La ventana sigue mientras hay avance: lo que se perdió en un POST sale
    // intacto en el siguiente, sin esperar al próximo lote.
    expect(window.freed).toBe(generated)
    expect(device.sd.pending).toHaveLength(0)
  })

  it('el reinicio no pierde el backlog: sale después, separado por arranque', () => {
    const device = freshDevice()
    const backend = new FakeIngest()

    let generated = record(device)
    reboot(device)
    generated += record(device)
    transmit(device, backend, {}, 0)

    expectCompleteStudy(backend, device, generated)
  })

  it('el CRC roto tampoco pierde señal: la trama se retransmite intacta', () => {
    const { backend, device, generated } = runVest(2, { corruptCrcPct: 25 })

    expectCompleteStudy(backend, device, generated)
  })

  it('los duplicados no adelantan ni atrasan el cursor', () => {
    const { backend, device, generated } = runVest(2, { duplicatePct: 50 })

    expectCompleteStudy(backend, device, generated)
  })
})

describe('recuperación después de perder la flash', () => {
  /** Se borró el almacenamiento del navegador: el reloj vuelve, la flash no. */
  function reloadedVest() {
    const device = freshDevice()
    const backend = new FakeIngest()
    record(device)
    // Un solo POST, con pérdidas: el backend confirma hasta el primer hueco.
    const frames = nextPostFrames(device.sd.pending, BRIDGE_POST_FRAMES)
    const channel = applyChannel(frames, { ...CLEAN, dropPct: 40 }, makeRng(77))
    applyAck(device.sd, frames, backend.post(channel.body).lastAcceptedSeq)
    expect(device.sd.pending.length).toBeGreaterThan(0)

    // El equipo vuelve con el cursor donde estaba, pero sin las tramas.
    device.sd.pending = []
    return { device, backend }
  }

  it('sin reiniciar, el backend espera un hueco que ya no existe', () => {
    const { device, backend } = reloadedVest()
    const before = backend.lastIngestedSeq

    record(device)
    transmit(device, backend, {}, 1)

    expect(backend.lastIngestedSeq).toBe(before)
  })

  it('reiniciar el equipo salta el hueco y el estudio vuelve a crecer', () => {
    const { device, backend } = reloadedVest()
    const before = backend.lastIngestedSeq!

    reboot(device)
    record(device)
    transmit(device, backend, {}, 1)

    expect(backend.lastIngestedSeq).toBeGreaterThan(before)
    // La seq no rebobina: el hueco queda registrado, no se pisa lo archivado.
    expect(Math.min(...[...backend.stored].filter((seq) => seq > before))).toBeGreaterThan(before)
  })
})
