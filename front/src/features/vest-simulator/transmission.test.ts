import { describe, expect, it } from 'vitest'

import { FRAME_BYTES, HDR_BOOTID_SHIFT } from './codec/frame'
import type { DeviceStorage, PendingFrame } from './deviceClock'
import {
  EMPTY_DIAG,
  INITIAL_BACKOFF,
  INITIAL_PLACEMENT,
  MAX_ACK_LOOKAHEAD_FRAMES,
  NO_BACKOFF_FROM_FRAMES,
  accumulateDiag,
  applyAck,
  diagHeaders,
  evaluatePlacement,
  nextPostFrames,
  onWindowFailed,
  responseStoredData,
} from './transmission'

function frame(seq: number, bootId = 0): PendingFrame {
  const bytes = new Uint8Array(FRAME_BYTES)
  bytes[3] = bootId << HDR_BOOTID_SHIFT
  return { seq, bytes, attempts: 0 }
}

function flash(seqs: number[], bootId = 0): DeviceStorage {
  return { pending: seqs.map((seq) => frame(seq, bootId)), overflowed: 0 }
}

function range(from: number, count: number): number[] {
  return Array.from({ length: count }, (_, i) => from + i)
}

describe('armado de POSTs del puente', () => {
  it('manda de a 48 desde la más vieja sin confirmar', () => {
    const sd = flash(range(10, 200))

    const post = nextPostFrames(sd.pending, 48)

    expect(post.map((f) => f.seq)).toEqual(range(10, 48))
  })

  it('corta en la primera trama de otro arranque', () => {
    // Un POST lleva un solo par epoch/uptime: no puede mezclar arranques.
    const pending = [...range(0, 5).map((s) => frame(s, 3)), ...range(5, 5).map((s) => frame(s, 4))]

    expect(nextPostFrames(pending, 48).map((f) => f.seq)).toEqual(range(0, 5))
    expect(nextPostFrames(pending.slice(5), 48).map((f) => f.seq)).toEqual(range(5, 5))
  })
})

describe('ACK del backend', () => {
  it('libera hasta la seq confirmada', () => {
    const sd = flash(range(0, 48))

    const outcome = applyAck(sd, sd.pending.slice(0, 48), 20)

    expect(outcome).toEqual({ freed: 21, inexplicable: false })
    expect(sd.pending[0].seq).toBe(21)
  })

  it('recorta un ACK que se pasa del final de lo enviado', () => {
    const sd = flash(range(0, 100))
    const sent = sd.pending.slice(0, 48)

    expect(applyAck(sd, sent, 70).freed).toBe(48)
    expect(sd.pending[0].seq).toBe(48)
  })

  it('ignora un ACK que confirma tramas que nunca salieron', () => {
    const sd = flash(range(0, 100))
    const sent = sd.pending.slice(0, 48)

    const outcome = applyAck(sd, sent, 48 + MAX_ACK_LOOKAHEAD_FRAMES + 5)

    expect(outcome).toEqual({ freed: 0, inexplicable: true })
    expect(sd.pending).toHaveLength(100)
  })

  it('un ACK sin seq no confirma nada', () => {
    const sd = flash(range(0, 10))

    expect(applyAck(sd, sd.pending, null).freed).toBe(0)
  })

  it('solo una respuesta con señal nueva limpia el diagnóstico', () => {
    expect(responseStoredData({ framesAccepted: 48, framesDuplicate: 0 })).toBe(true)
    expect(responseStoredData({ framesAccepted: 48, framesDuplicate: 48 })).toBe(false)
  })
})

describe('cabeceras X-Device-*', () => {
  it('acumula con OR, máximo y peor SQI', () => {
    let diag = accumulateDiag(EMPTY_DIAG, { leadFlags: 0x01, backlogSeconds: 30, worstSqi: 3 })
    diag = accumulateDiag(diag, { leadFlags: 0x20, backlogSeconds: 10, worstSqi: 2 })
    diag = accumulateDiag(diag, { statusFlags: 0x80, batteryFlags: 0x01 })

    expect(diag).toMatchObject({
      leadFlags: 0x21,
      statusFlags: 0x80,
      backlogSeconds: 30,
      worstSqi: 2,
      batteryFlags: 0x01,
    })
  })

  it('sin nada acumulado solo van el RSSI y lo conocido', () => {
    expect(diagHeaders(EMPTY_DIAG, -60)).toEqual({ 'X-Device-Rssi': '-60' })
  })

  it('con datos van todas, en el rango del backend', () => {
    const diag = accumulateDiag(EMPTY_DIAG, {
      leadFlags: 0x21,
      backlogSeconds: 120,
      worstSqi: 1,
      batteryFlags: 0x03,
    })

    expect(diagHeaders(diag, -71)).toEqual({
      'X-Device-Lead-Flags': '33',
      'X-Device-Loss-Flags': '0',
      'X-Device-Status-Flags': '0',
      'X-Device-Backlog-Seconds': '120',
      'X-Device-Sqi': '1',
      'X-Device-Rssi': '-71',
      'X-Device-Battery-Flags': '3',
    })
  })
})

describe('backoff de ventanas', () => {
  it('10 → 20 → 40 min, en lotes de 10 min', () => {
    let backoff = onWindowFailed(INITIAL_BACKOFF, 100, 10)
    expect(backoff.skipWindows).toBe(0)
    backoff = onWindowFailed(backoff, 100, 10)
    expect(backoff.skipWindows).toBe(1)
    backoff = onWindowFailed(backoff, 100, 10)
    expect(backoff.skipWindows).toBe(3)
    backoff = onWindowFailed(backoff, 100, 10)
    expect(backoff.skipWindows).toBe(3)
  })

  it('con la flash al 90 % no hay backoff', () => {
    const backoff = onWindowFailed({ failures: 5, skipWindows: 0 }, NO_BACKOFF_FROM_FRAMES, 10)

    expect(backoff.skipWindows).toBe(0)
  })
})

describe('aviso de colocación', () => {
  const START = Date.UTC(2026, 9, 3, 12)

  function seconds(spec: [number, number][]): Uint8Array {
    return Uint8Array.from(spec.flatMap(([status, count]) => new Array(count).fill(status)))
  }

  it('avisa tras 120 s de electrodo suelto y cierra tras 60 s buenos', () => {
    const { notices, state } = evaluatePlacement(
      INITIAL_PLACEMENT,
      seconds([
        [0, 30],
        [1, 150],
        [0, 90],
      ]),
      START,
    )

    expect(notices.map((n) => n.event)).toEqual(['lead_off', 'signal_recovered'])
    expect(notices[0].durationSeconds).toBe(120)
    expect(notices[0].atMs).toBe(START + 149_000)
    expect(state.reported).toBeNull()
  })

  it('un rebote corto no avisa', () => {
    const { notices } = evaluatePlacement(
      INITIAL_PLACEMENT,
      seconds([
        [1, 90],
        [0, 60],
      ]),
      START,
    )

    expect(notices).toEqual([])
  })

  it('la condición se arrastra entre lotes', () => {
    const first = evaluatePlacement(INITIAL_PLACEMENT, seconds([[2, 80]]), START)
    const second = evaluatePlacement(first.state, seconds([[2, 80]]), START + 80_000)

    expect(first.notices).toEqual([])
    expect(second.notices.map((n) => n.event)).toEqual(['signal_quality_bad'])
  })

  it('no repite un aviso antes de 5 min', () => {
    const { notices } = evaluatePlacement(
      INITIAL_PLACEMENT,
      seconds([
        [1, 130],
        [0, 65],
        [1, 130],
      ]),
      START,
    )

    // El segundo episodio cumple 120 s a los ~315 s, pero el primer aviso fue
    // a los 119: recién puede repetir a los 419.
    expect(notices.map((n) => n.event)).toEqual(['lead_off', 'signal_recovered'])
  })
})
