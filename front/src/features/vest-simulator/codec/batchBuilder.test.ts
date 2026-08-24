import { describe, expect, it } from 'vitest'

import { buildBatch, splitFrames } from './batchBuilder'
import { decodeFrame } from './riceDecoder'
import { FRAME_BYTES, readHeader } from './frame'
import { DEFAULT_SIGNAL_CONFIG } from './signal'

function build(durationSec = 20, simulated = true) {
  return buildBatch({
    requestId: 1,
    signal: { ...DEFAULT_SIGNAL_CONFIG, durationSec, seed: 5 },
    firstSeq: 100,
    bootId: 3,
    t0Ms: 0,
    simulated,
  })
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
    // aire sigue existiendo en la SD.
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

  it('es reproducible: misma configuración, mismos bytes', () => {
    expect(new Uint8Array(build().body)).toEqual(new Uint8Array(build().body))
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
    // que depende del ruido configurado.
    const framesPerSecond = batch.framesGenerated / 60
    expect(framesPerSecond).toBeGreaterThan(0.5)
    expect(framesPerSecond).toBeLessThan(6)
    expect(batch.sampleCount).toBe(60 * 500)
  })
})
