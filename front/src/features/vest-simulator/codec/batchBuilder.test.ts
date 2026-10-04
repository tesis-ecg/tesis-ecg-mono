import { describe, expect, it } from 'vitest'

import { buildBatch, splitFrames } from './batchBuilder'
import { decodeFrame } from './riceDecoder'
import { FRAME_BYTES, readHeader } from './frame'
import { batchRequest } from './testSignals'

function build(durationSec = 20, simulated = true) {
  return buildBatch(batchRequest({ durationSec, simulated }))
}

describe('generación de lotes', () => {
  it('produce un cuerpo múltiplo de 256 bytes', () => {
    const batch = build()

    expect(batch.body.byteLength % FRAME_BYTES).toBe(0)
    expect(batch.framesGenerated).toBe(batch.body.byteLength / FRAME_BYTES)
  })

  it('numera las tramas desde firstSeq y respeta el bootId', () => {
    const batch = build()
    const headers = splitFrames(batch.body).map(readHeader)

    expect(headers[0].seq).toBe(100)
    expect(headers.every((h) => h.bootId === 3)).toBe(true)
    expect(batch.lastSeq).toBe(100 + batch.framesGenerated - 1)
  })

  it('lo que sale del generador está limpio: sin huecos ni CRC roto', () => {
    // Las anomalías de transmisión viven en `channel.ts`. Que acá salga siempre
    // un lote íntegro es lo que permite retransmitir: una trama perdida en el
    // aire sigue existiendo en la flash.
    const batch = build()
    const frames = splitFrames(batch.body)

    for (const frame of frames) expect(() => decodeFrame(frame)).not.toThrow()
    const seqs = frames.map((f) => readHeader(f).seq)
    expect(seqs).toEqual(seqs.map((_, i) => 100 + i))
  })

  it('apagar el bit de simulado marca las tramas como clínicas', () => {
    const batch = build(20, false)

    expect(splitFrames(batch.body).every((f) => !readHeader(f).simulated)).toBe(true)
  })

  it('es reproducible: misma configuración y mismo estado, mismos bytes', () => {
    expect(new Uint8Array(build().body)).toEqual(new Uint8Array(build().body))
  })

  it('devuelve el estado del generador para que el lote siguiente continúe', () => {
    const first = build()
    const second = buildBatch(batchRequest({ genState: first.genState, t0Ms: 20_000 }))

    // Antes cada lote arrancaba de la misma semilla: todos eran idénticos.
    expect(new Uint8Array(second.body)).not.toEqual(new Uint8Array(first.body))
    expect(second.genState.t).toBeCloseTo(40, 6)
    expect(first.secondStatus).toHaveLength(20)
  })
})

describe('volumen', () => {
  it('reporta el tamaño sin comprimir para poder medir el ratio', () => {
    const batch = build(60)

    expect(batch.uncompressedBytes).toBe(batch.sampleCount * 4)
    expect(batch.body.byteLength).toBeLessThan(batch.uncompressedBytes)
  })

  it('un minuto de señal a 500 Hz da del orden de 100 tramas', () => {
    const batch = build(60)

    // Cota amplia: lo que se está fijando es el orden de magnitud del caudal,
    // que depende de la red configurada.
    const framesPerSecond = batch.framesGenerated / 60
    expect(framesPerSecond).toBeGreaterThan(0.5)
    expect(framesPerSecond).toBeLessThan(6)
    expect(batch.sampleCount).toBe(60 * 500)
  })
})
