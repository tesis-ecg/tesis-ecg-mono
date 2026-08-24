import { describe, expect, it } from 'vitest'

import {
  CRC_OFFSET,
  FRAME_BYTES,
  FRAME_MAGIC,
  FRAME_VERSION,
  HEADER_BYTES,
  MAX_FLAG_RUNS,
  RUN_BYTES,
  corruptCrc,
  crc32,
  frameCrc,
  readHeader,
  withSeq,
} from './frame'
import { encodeSamples } from './riceEncoder'
import { flatSamples } from './testSignals'

describe('crc32', () => {
  it('reproduce los vectores conocidos del CRC-32 IEEE 802.3', () => {
    // Los mismos que devuelve `zlib.crc32` en el backend.
    const of = (text: string) => (crc32(new TextEncoder().encode(text)) ^ 0xffffffff) >>> 0
    expect(of('')).toBe(0)
    expect(of('a')).toBe(0xe8b7be43)
    expect(of('abc')).toBe(0x352441c2)
    expect(of('123456789')).toBe(0xcbf43926)
  })
})

describe('cabecera', () => {
  it('escribe el layout exacto byte a byte', () => {
    const frame = encodeSamples(flatSamples(200), {
      firstSeq: 0x11223344,
      bootId: 9,
      simulated: true,
    })[0]
    const view = new DataView(frame.buffer)

    expect(frame.length).toBe(FRAME_BYTES)
    expect(view.getUint16(0, true)).toBe(FRAME_MAGIC)
    expect(frame[2]).toBe(FRAME_VERSION)
    expect(frame[3] & 0x08).toBe(0x08) // bit de DATO SIMULADO
    expect((frame[3] & 0xf0) >> 4).toBe(9) // bootId
    expect(view.getUint32(4, true)).toBe(0x11223344)
    expect(frame[19]).toBe(1) // un solo stream con una derivación
  })

  it('deja el CRC en el offset 20 y valida', () => {
    const frame = encodeSamples(flatSamples(200))[0]
    const view = new DataView(frame.buffer)

    expect(view.getUint32(CRC_OFFSET, true)).toBe(frameCrc(frame))
  })

  it('el CRC cubre el relleno en cero', () => {
    const frame = encodeSamples(flatSamples(20))[0]
    const header = readHeader(frame)
    const paddingOffset = HEADER_BYTES + header.bitBytes + 4
    expect(frame[paddingOffset]).toBe(0)

    const dirty = frame.slice()
    dirty[paddingOffset] = 0xff

    expect(frameCrc(dirty)).not.toBe(header.crc32)
  })

  it('corruptCrc rompe la validación sin tocar el resto', () => {
    const frame = encodeSamples(flatSamples(200))[0]
    const broken = corruptCrc(frame)

    expect(readHeader(broken).crc32).not.toBe(frameCrc(broken))
    expect(broken.subarray(0, CRC_OFFSET)).toEqual(frame.subarray(0, CRC_OFFSET))
  })

  it('withSeq reescribe el número y deja el CRC válido', () => {
    const frame = encodeSamples(flatSamples(200))[0]

    const renumbered = withSeq(frame, 4242)

    expect(readHeader(renumbered).seq).toBe(4242)
    expect(readHeader(renumbered).crc32).toBe(frameCrc(renumbered))
  })
})

describe('corridas RLE de flags', () => {
  it('viven al final de la trama y suman n_samples', () => {
    const samples = flatSamples(400)
    for (let i = 100; i < 200; i++) samples[i].flags = 0x01
    const frame = encodeSamples(samples)[0]
    const header = readHeader(frame)

    const runsOffset = FRAME_BYTES - header.runCount * RUN_BYTES
    const view = new DataView(frame.buffer)
    let total = 0
    for (let i = 0; i < header.runCount; i++) {
      total += view.getUint16(runsOffset + i * RUN_BYTES + 1, true)
    }

    expect(total).toBe(header.nSamples)
    expect(header.runCount).toBeLessThanOrEqual(MAX_FLAG_RUNS)
  })

  it('nunca supera las 24 corridas: cierra la trama antes', () => {
    const samples = flatSamples(4000)
    samples.forEach((sample, i) => {
      sample.flags = i % 2
    })

    const frames = encodeSamples(samples)

    for (const frame of frames) {
      expect(readHeader(frame).runCount).toBeLessThanOrEqual(MAX_FLAG_RUNS)
    }
  })
})
