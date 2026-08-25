import { describe, expect, it } from 'vitest'

import {
  ackUpTo,
  acquireDevice,
  advanceClock,
  backlogBytes,
  forgetClock,
  reboot,
  recordFrames,
  takeWindow,
  MAX_BACKLOG_FRAMES,
  MAX_FRAMES_PER_REQUEST,
  type ClockRegistry,
  type DeviceStorage,
} from './deviceClock'
import { makeVestConfig } from './defaults'
import { BOOTID_MODULO, FRAME_BYTES } from './codec/frame'

/** Una corrida de `n` lotes de 1800 tramas, como las manda el hook. */
function runBatches(registry: ClockRegistry, id: string, batches: number) {
  const config = { ...makeVestConfig(), id, batchMinutes: 30 }
  const { clock } = acquireDevice(registry, id, config)
  const seqs: number[] = []
  for (let i = 0; i < batches; i++) {
    seqs.push(clock.nextSeq)
    advanceClock(clock, { lastSeq: clock.nextSeq + 1799, sampleCount: 900_000 }, 30)
  }
  return seqs
}

function emptySd(): DeviceStorage {
  return { pending: [], overflowed: 0 }
}

/** `n` tramas de relleno: acá solo importan los `seq`, no los bytes. */
function fakeFrames(n: number): Uint8Array[] {
  return Array.from({ length: n }, () => new Uint8Array(FRAME_BYTES))
}

describe('reloj del equipo simulado', () => {
  it('la segunda corrida sigue el cursor de la primera', () => {
    const registry: ClockRegistry = new Map()

    const first = runBatches(registry, 'vest-1', 1)
    const second = runBatches(registry, 'vest-1', 1)

    // Rebobinar a 0 con el mismo bootId es lo que el backend lee como
    // retransmisión: confirma el lote sin guardarlo y el estudio no crece.
    expect(first[0]).toBe(0)
    expect(second[0]).toBe(1800)
    expect(registry.get('vest-1')!.clock.bootId).toBe(0)
  })

  it('cada chaleco lleva su propio cursor', () => {
    const registry: ClockRegistry = new Map()

    runBatches(registry, 'vest-1', 3)
    const other = runBatches(registry, 'vest-2', 1)

    expect(other[0]).toBe(0)
    expect(registry.get('vest-1')!.clock.nextSeq).toBe(5400)
  })

  it('quitar el chaleco olvida su reloj', () => {
    const registry: ClockRegistry = new Map()

    runBatches(registry, 'vest-1', 2)
    forgetClock(registry, 'vest-1')

    expect(runBatches(registry, 'vest-1', 1)[0]).toBe(0)
  })

  it('retoma un reloj restaurado de una sesión anterior', () => {
    // Sin esto, un F5 devolvía el equipo a `seq 0 / bootId 0`: un estado que el
    // hardware no puede producir y que el backend solo puede leer como una
    // retransmisión completa del estudio.
    const registry: ClockRegistry = new Map()
    const restored = { bootId: 2, nextSeq: 90_000, t0Ms: 500, uptimeMs: 7_200_000, batteryPct: 61 }

    const { clock } = acquireDevice(registry, 'vest-1', makeVestConfig(), restored)

    expect(clock.nextSeq).toBe(90_000)
    expect(clock.bootId).toBe(2)
  })

  it('el uptime acumula entre corridas para que el ancla temporal no retroceda', () => {
    const registry: ClockRegistry = new Map()

    runBatches(registry, 'vest-1', 1)
    const afterFirst = registry.get('vest-1')!.clock.uptimeMs
    runBatches(registry, 'vest-1', 1)

    expect(afterFirst).toBe(2 * 30 * 60_000)
    expect(registry.get('vest-1')!.clock.uptimeMs).toBe(3 * 30 * 60_000)
  })

  it('el reinicio cambia el bootId y pone el reloj en cero, pero no rebobina el seq', () => {
    const registry: ClockRegistry = new Map()
    runBatches(registry, 'vest-1', 2)
    const device = registry.get('vest-1')!
    const cursor = device.clock.nextSeq
    recordFrames(device.sd, fakeFrames(5), cursor)

    const lost = reboot(device)

    expect(device.clock.bootId).toBe(1 % BOOTID_MODULO)
    expect(device.clock.t0Ms).toBe(0)
    expect(device.clock.uptimeMs).toBe(0)
    // Los segmentos del estudio se nombran en S3 con el `first_seq` del lote y
    // sin el `bootId`: volver a 0 pisaría los del boot anterior.
    expect(device.clock.nextSeq).toBe(cursor)
    // Las tramas del boot anterior ya tienen su bootId escrito en la cabecera:
    // reenviarlas mezclaría dos boots bajo una sola ancla temporal.
    expect(lost).toBe(5)
    expect(device.sd.pending).toHaveLength(0)
  })

  it('la batería baja por lote con piso en 5 %', () => {
    const registry: ClockRegistry = new Map()
    runBatches(registry, 'vest-1', 400)

    expect(registry.get('vest-1')!.clock.batteryPct).toBe(5)
  })
})

describe('SD del equipo', () => {
  it('graba las tramas con su seq y sin intentos', () => {
    const sd = emptySd()

    recordFrames(sd, fakeFrames(3), 500)

    expect(sd.pending.map((f) => f.seq)).toEqual([500, 501, 502])
    expect(sd.pending.every((f) => f.attempts === 0)).toBe(true)
    expect(backlogBytes(sd)).toBe(3 * FRAME_BYTES)
  })

  it('el ACK libera solo hasta la seq confirmada', () => {
    // El resto queda para el ciclo siguiente. Liberar de más sería exactamente
    // el bug que dejaba el estudio congelado: el equipo borraba de su SD señal
    // que el backend nunca guardó.
    const sd = emptySd()
    recordFrames(sd, fakeFrames(10), 100)

    const freed = ackUpTo(sd, 103)

    expect(freed).toBe(4)
    expect(sd.pending.map((f) => f.seq)).toEqual([104, 105, 106, 107, 108, 109])
  })

  it('un ACK sin seq confirmada no libera nada', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(4), 0)

    expect(ackUpTo(sd, null)).toBe(0)
    expect(sd.pending).toHaveLength(4)
  })

  it('un ACK viejo no libera tramas posteriores al hueco', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(6), 200)
    ackUpTo(sd, 201)

    expect(ackUpTo(sd, 201)).toBe(0)
    expect(sd.pending[0].seq).toBe(202)
  })

  it('desbordar el backlog descarta lo más viejo y lo cuenta como pérdida', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(MAX_BACKLOG_FRAMES), 0)

    const lost = recordFrames(sd, fakeFrames(10), MAX_BACKLOG_FRAMES)

    expect(lost).toBe(10)
    expect(sd.overflowed).toBe(10)
    expect(sd.pending).toHaveLength(MAX_BACKLOG_FRAMES)
    expect(sd.pending[0].seq).toBe(10)
  })

  it('la ventana arranca en la trama más vieja sin confirmar y entra en un request', () => {
    const sd = emptySd()
    recordFrames(sd, fakeFrames(MAX_FRAMES_PER_REQUEST + 500), 0)
    ackUpTo(sd, 9)

    const window = takeWindow(sd)

    expect(window).toHaveLength(MAX_FRAMES_PER_REQUEST)
    expect(window[0].seq).toBe(10)
  })
})
