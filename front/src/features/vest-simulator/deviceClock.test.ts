import { describe, expect, it } from 'vitest'

import { buildBatch, splitFrames } from './codec/batchBuilder'
import { BOOTID_MODULO, FRAME_BYTES, readHeader } from './codec/frame'
import { batchRequest } from './codec/testSignals'
import {
  ackUpTo,
  acquireDevice,
  advanceClock,
  anchorFirstRun,
  backlogBytes,
  bridgeTimeForPost,
  bridgeTimeNow,
  dataCursorEpochMs,
  firstRunLeadMs,
  forgetClock,
  initialClock,
  reboot,
  recordFrames,
  FLASH_SECTOR_FRAMES,
  MAX_BACKLOG_FRAMES,
  WARMUP_MS,
  type ClockRegistry,
  type DeviceRuntime,
  type DeviceStorage,
  type PendingFrame,
} from './deviceClock'
import { makeVestConfig } from './defaults'

const NOW = Date.UTC(2026, 9, 3, 18)

/** Graba un lote de `seconds` de señal real, como lo hace el hook. */
function record(device: DeviceRuntime, seconds = 20): PendingFrame[] {
  const { clock } = device
  const firstSeq = clock.nextSeq
  const built = buildBatch(
    batchRequest({
      durationSec: seconds,
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
  return device.sd.pending.slice(-built.framesGenerated)
}

function emptySd(): DeviceStorage {
  return { pending: [], overflowed: 0 }
}

/** `n` tramas de relleno: acá solo importan los `seq`, no los bytes. */
function fakeFrames(n: number): Uint8Array[] {
  return Array.from({ length: n }, () => new Uint8Array(FRAME_BYTES))
}

function freshDevice(now = NOW): DeviceRuntime {
  return { clock: initialClock(now), sd: emptySd() }
}

describe('cursor del equipo simulado', () => {
  it('la segunda corrida sigue el cursor de la primera', () => {
    const registry: ClockRegistry = new Map()
    const device = acquireDevice(registry, 'vest-1')
    record(device)
    const after = device.clock.nextSeq

    // Mismo equipo: rebobinar a 0 con el mismo bootId es lo que el backend lee
    // como retransmisión.
    expect(acquireDevice(registry, 'vest-1')).toBe(device)
    record(device)
    expect(device.sd.pending[0].seq).toBe(0)
    expect(device.clock.nextSeq).toBeGreaterThan(after)
  })

  it('cada chaleco lleva su propio cursor y quitarlo lo olvida', () => {
    const registry: ClockRegistry = new Map()
    record(acquireDevice(registry, 'vest-1'))

    expect(acquireDevice(registry, 'vest-2').clock.nextSeq).toBe(0)
    forgetClock(registry, 'vest-1')
    expect(acquireDevice(registry, 'vest-1').clock.nextSeq).toBe(0)
  })

  it('retoma un reloj restaurado de una sesión anterior', () => {
    const registry: ClockRegistry = new Map()
    const restored = { ...initialClock(NOW), bootId: 2, nextSeq: 90_000, fresh: false }

    const { clock } = acquireDevice(registry, 'vest-1', restored)

    expect(clock.nextSeq).toBe(90_000)
    expect(clock.bootId).toBe(2)
  })

  it('la batería baja ~0,42 %/h de señal, con piso', () => {
    const clock = initialClock(NOW)
    advanceClock(clock, { lastSeq: 0, sampleCount: 500 * 3600 })

    expect(clock.batteryPct).toBeCloseTo(96 - 100 / 240, 3)
    advanceClock(clock, { lastSeq: 0, sampleCount: 500 * 3600 * 1000 })
    expect(clock.batteryPct).toBe(3)
  })
})

describe('primera corrida: la señal termina ahora', () => {
  const base = { batchMinutes: 10, batchCount: 3 }

  it('en modo instantáneo la corrida entera queda en el pasado', () => {
    expect(firstRunLeadMs({ ...base, cadence: { kind: 'instant' } })).toBe(30 * 60_000)
  })

  it('en tiempo real alcanza con un lote: los demás se graban mientras pasa el tiempo', () => {
    expect(firstRunLeadMs({ ...base, cadence: { kind: 'realtime' } })).toBe(10 * 60_000)
  })

  it('en modo acelerado el último lote termina justo cuando se manda', () => {
    const lead = firstRunLeadMs({ ...base, cadence: { kind: 'accelerated', factor: 60 } })
    const batchMs = 10 * 60_000
    // El lote k sale k·B/f después de arrancar y termina (k+1)·B después del
    // inicio de la señal.
    const lastEnd = NOW - lead + 3 * batchMs
    const lastSentAt = NOW + (2 * batchMs) / 60

    expect(lastEnd).toBeCloseTo(lastSentAt, 6)
  })

  it('ancla el reloj una sola vez', () => {
    const clock = initialClock(NOW - 5 * 3_600_000)
    const config = { ...makeVestConfig(), ...base }

    expect(anchorFirstRun(clock, config, NOW)).toBe(true)
    expect(dataCursorEpochMs(clock)).toBe(NOW - 30 * 60_000)
    expect(clock.t0Ms).toBe(WARMUP_MS)
    expect(clock.bootAnchors[0]).toBe(clock.bootEpochMs)

    expect(anchorFirstRun(clock, config, NOW + 3_600_000)).toBe(false)
    expect(dataCursorEpochMs(clock)).toBe(NOW - 30 * 60_000)
  })
})

describe('hora del puente', () => {
  it('después de horas sin usar el chaleco manda la hora real y la señal sigue donde quedó', () => {
    // El caso del 422: el chaleco grabó por última vez hace 12 h. Antes el
    // epoch salía de `arranque + uptime simulado` y quedaba 12 h atrás.
    const device = freshDevice(NOW - 12 * 3_600_000)
    anchorFirstRun(
      device.clock,
      { batchMinutes: 10, batchCount: 1, cadence: { kind: 'instant' } },
      NOW - 12 * 3_600_000,
    )
    const frames = record(device)
    const firstHeader = readHeader(frames[0].bytes)

    const time = bridgeTimeForPost(device.clock, frames, NOW)

    expect(time.epochMs).toBe(NOW)
    expect(time.aheadMs).toBe(0)
    expect(time.bootId).toBe(0)
    // Lo que calcula el backend: arranque = epoch − uptime; muestra = arranque + t0.
    const bootEpoch = time.epochMs - time.uptimeMs
    expect(bootEpoch).toBe(device.clock.bootEpochMs)
    const firstSampleUtc = bootEpoch + firstHeader.t0Ms
    expect(firstSampleUtc).toBe(NOW - 12 * 3_600_000 - 10 * 60_000)
  })

  it('si la señal va adelante de la hora real, adelanta el epoch lo justo', () => {
    const device = freshDevice(NOW)
    device.clock.fresh = false
    const frames = record(device, 20)
    const last = readHeader(frames[frames.length - 1].bytes)

    const time = bridgeTimeForPost(device.clock, frames, NOW)

    const lastSampleUtc = device.clock.bootEpochMs + last.t0Ms + last.durationMs
    expect(time.epochMs).toBeGreaterThan(lastSampleUtc)
    expect(time.aheadMs).toBe(time.epochMs - NOW)
    expect(time.epochMs - time.uptimeMs).toBe(device.clock.bootEpochMs)
  })

  it('el backlog de un arranque anterior viaja con la hora de su arranque', () => {
    const device = freshDevice(NOW - 3_600_000)
    device.clock.fresh = false
    const oldFrames = record(device)
    const oldBootEpoch = device.clock.bootEpochMs

    reboot(device)
    record(device)

    const time = bridgeTimeForPost(device.clock, oldFrames, NOW)
    expect(time.bootId).toBe(0)
    expect(time.epochMs - time.uptimeMs).toBe(oldBootEpoch)

    // Sin la tabla de arranques, el puente manda el par del arranque actual.
    const lost = bridgeTimeForPost(device.clock, oldFrames, NOW, {
      noSntp: false,
      lostBootTable: true,
    })
    expect(lost.bootId).toBe(1)
    expect(lost.epochMs - lost.uptimeMs).toBe(device.clock.bootEpochMs)
  })

  it('sin SNTP la fuente es none y la incertidumbre de un Date por HTTP', () => {
    const device = freshDevice()
    const ntp = bridgeTimeNow(device.clock, NOW)
    const none = bridgeTimeNow(device.clock, NOW, { noSntp: true, lostBootTable: false })

    expect(ntp).toMatchObject({ source: 'ntp', uncertaintyMs: 200 })
    expect(none.source).toBe('none')
    expect(none.uncertaintyMs).toBeGreaterThanOrEqual(1000)
  })
})

describe('reinicio', () => {
  it('cambia el bootId y vuelve el reloj a cero, sin rebobinar la seq ni vaciar la flash', () => {
    const device = freshDevice()
    device.clock.fresh = false
    record(device)
    const cursor = device.clock.nextSeq
    const pending = device.sd.pending.length
    const dataEnd = dataCursorEpochMs(device.clock)

    reboot(device)

    expect(device.clock.bootId).toBe(1 % BOOTID_MODULO)
    expect(device.clock.t0Ms).toBe(0)
    // Los segmentos del estudio se nombran en S3 con el `first_seq` del lote.
    expect(device.clock.nextSeq).toBe(cursor)
    // La flash sobrevive al corte de energía.
    expect(device.sd.pending).toHaveLength(pending)
    // El arranque nuevo empieza después del último dato, y queda en la tabla.
    expect(device.clock.bootEpochMs).toBeGreaterThan(dataEnd)
    expect(device.clock.bootAnchors[1]).toBe(device.clock.bootEpochMs)
  })
})

describe('flash del equipo', () => {
  it('graba las tramas con su seq y sin intentos', () => {
    const sd = emptySd()

    recordFrames(sd, fakeFrames(3), 500)

    expect(sd.pending.map((f) => f.seq)).toEqual([500, 501, 502])
    expect(sd.pending.every((f) => f.attempts === 0)).toBe(true)
    expect(backlogBytes(sd)).toBe(3 * FRAME_BYTES)
  })

  it('el ACK libera solo hasta la seq confirmada', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(10), 100)

    expect(ackUpTo(sd, 103)).toBe(4)
    expect(sd.pending.map((f) => f.seq)).toEqual([104, 105, 106, 107, 108, 109])
    expect(ackUpTo(sd, null)).toBe(0)
    expect(ackUpTo(sd, 103)).toBe(0)
  })

  it('desbordar expulsa lo más viejo por sector de 16 tramas y lo cuenta como pérdida', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(MAX_BACKLOG_FRAMES), 0)

    const lost = recordFrames(sd, fakeFrames(10), MAX_BACKLOG_FRAMES)

    expect(lost).toBe(FLASH_SECTOR_FRAMES)
    expect(sd.overflowed).toBe(FLASH_SECTOR_FRAMES)
    expect(sd.pending).toHaveLength(MAX_BACKLOG_FRAMES + 10 - FLASH_SECTOR_FRAMES)
    expect(sd.pending[0].seq).toBe(FLASH_SECTOR_FRAMES)
  })
})
