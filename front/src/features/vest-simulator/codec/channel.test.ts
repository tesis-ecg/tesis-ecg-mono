import { describe, expect, it } from 'vitest'

import { applyChannel, makeRng } from './channel'
import { buildBatch, splitFrames } from './batchBuilder'
import { decodeFrame } from './riceDecoder'
import { FRAME_BYTES, readHeader } from './frame'
import { DEFAULT_SIGNAL_CONFIG } from './signal'
import type { PendingFrame } from '../deviceClock'
import type { FrameAnomalies } from '../types'

const CLEAN: FrameAnomalies = {
  corruptCrcPct: 0,
  duplicatePct: 0,
  dropPct: 0,
  rebootAtBatch: 0,
  simulated: true,
  shuffle: false,
}

/** Una SD con las tramas de 20 s de señal, todas sin intentar. */
function pending(firstSeq = 100): PendingFrame[] {
  const batch = buildBatch({
    requestId: 1,
    signal: { ...DEFAULT_SIGNAL_CONFIG, durationSec: 20, seed: 5 },
    firstSeq,
    bootId: 3,
    t0Ms: 0,
    simulated: true,
  })
  return splitFrames(batch.body).map((bytes, i) => ({ seq: firstSeq + i, bytes, attempts: 0 }))
}

function framesOf(body: Uint8Array): Uint8Array[] {
  const out: Uint8Array[] = []
  for (let i = 0; i < body.length; i += FRAME_BYTES) out.push(body.subarray(i, i + FRAME_BYTES))
  return out
}

function send(window: PendingFrame[], anomalies: Partial<FrameAnomalies> = {}, seed = 99) {
  return applyChannel(window, { ...CLEAN, ...anomalies }, makeRng(seed))
}

describe('canal de transmisión', () => {
  it('un canal limpio manda la ventana entera y en orden', () => {
    const window = pending()
    const result = send(window)

    expect(result.body.length).toBe(window.length * FRAME_BYTES)
    expect(framesOf(result.body).map((f) => readHeader(f).seq)).toEqual(window.map((f) => f.seq))
  })

  it('descartar tramas deja huecos reales en seq', () => {
    const result = send(pending(), { dropPct: 100 })

    expect(result.body.length).toBe(0)
    expect(result.droppedSeqs).toHaveLength(pending().length)
  })

  it('la pérdida es del primer intento: la retransmisión sale intacta', () => {
    // Es la regla que hace que el simulador sirva. Sin ella, con un 20 % de
    // pérdida sorteada en cada intento la ventana avanza ~4 tramas por request
    // y el backlog no drena nunca: el estudio queda tan congelado como antes.
    const window = pending()

    const first = send(window, { dropPct: 100 })
    const second = send(window, { dropPct: 100 })

    expect(first.body.length).toBe(0)
    expect(second.droppedSeqs).toHaveLength(0)
    expect(second.body.length).toBe(window.length * FRAME_BYTES)
    expect(window.every((frame) => frame.attempts === 2)).toBe(true)
  })

  it('el CRC roto también es del primer intento', () => {
    const window = pending()

    const first = send(window, { corruptCrcPct: 100 })
    for (const frame of framesOf(first.body)) expect(() => decodeFrame(frame)).toThrow(/CRC/)

    const second = send(window, { corruptCrcPct: 100 })
    expect(second.corruptedSeqs).toHaveLength(0)
    for (const frame of framesOf(second.body)) expect(() => decodeFrame(frame)).not.toThrow()
  })

  it('duplicar manda la misma trama dos veces, también al retransmitir', () => {
    const window = pending()

    const first = send(window, { duplicatePct: 100 })
    const second = send(window, { duplicatePct: 100 })

    for (const result of [first, second]) {
      expect(result.body.length).toBe(window.length * 2 * FRAME_BYTES)
      const seqs = framesOf(result.body).map((f) => readHeader(f).seq)
      expect(new Set(seqs).size).toBe(window.length)
    }
  })

  it('el shuffle desordena sin perder ninguna trama', () => {
    const ordered = send(pending())
    const shuffled = send(pending(), { shuffle: true })

    expect(shuffled.body.length).toBe(ordered.body.length)
    const seqs = framesOf(shuffled.body)
      .map((f) => readHeader(f).seq)
      .sort((a, b) => a - b)
    expect(seqs).toEqual(framesOf(ordered.body).map((f) => readHeader(f).seq))
  })

  it('marca la ventana como intentada aunque la trama no salga', () => {
    const window = pending()
    send(window, { dropPct: 100 })

    expect(window.every((frame) => frame.attempts === 1)).toBe(true)
  })
})
