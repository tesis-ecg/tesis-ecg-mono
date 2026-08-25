/**
 * Decodificador Rice en TypeScript.
 *
 * **Existe solo para los tests de round-trip del simulador.** El decodificador
 * de verdad — el que reconstruye la señal del paciente — vive en el backend
 * (`back/app/ml/decompression.py`) y es un port del decodificador normativo del
 * firmware. Este no está en el camino de ningún dato clínico.
 *
 * Un round-trip contra este decoder prueba que el encoder es autoconsistente,
 * no que respeta el formato: si los dos compartieran el mismo malentendido,
 * pasaría igual. Esa verificación la da el fixture dorado que consume pytest.
 */

import {
  FRAME_BYTES,
  FRAME_MAGIC,
  FRAME_VERSION,
  HEADER_BYTES,
  MAX_FLAG_RUNS,
  PREDICTOR_ORDER,
  RICE_ADAPT_SHIFT,
  RICE_ESCAPE_Q,
  RICE_K_MAX,
  RICE_RAW_BITS,
  RICE_SEED_MEAN,
  RUN_BYTES,
  STEP_MS,
  type FrameHeader,
  frameCrc,
  readHeader,
} from './frame'

export class FrameError extends Error {}

export interface DecodedFrame {
  header: FrameHeader
  /** `[canal][muestra]` en µV. */
  rawUV: Int32Array[]
  diagnosticUV: Int32Array[]
  flags: Uint8Array
  timestampsMs: Uint32Array
}

class BitReader {
  private pos = 0
  private readonly buf: Uint8Array
  private readonly limit: number

  constructor(buf: Uint8Array, limit: number) {
    this.buf = buf
    this.limit = limit
  }

  readBit(): number {
    if (this.pos >= this.limit) throw new FrameError('bitstream truncado')
    const bit = (this.buf[this.pos >> 3] >> (7 - (this.pos & 7))) & 1
    this.pos++
    return bit
  }

  readBits(n: number): number {
    let value = 0
    for (let i = 0; i < n; i++) value = value * 2 + this.readBit()
    return value
  }
}

class RiceStream {
  prev = 0
  prev2 = 0
  sum = RICE_SEED_MEAN << RICE_ADAPT_SHIFT
  primed = false
  primed2 = false

  k(): number {
    const mean = this.sum >>> RICE_ADAPT_SHIFT
    let k = 0
    while (k < RICE_K_MAX && 1 << (k + 1) <= mean) k++
    return k
  }

  predict(): number {
    if (PREDICTOR_ORDER >= 2 && this.primed2) return 2 * this.prev - this.prev2
    if (this.primed) return this.prev
    return 0
  }

  push(value: number): void {
    if (this.primed) {
      this.prev2 = this.prev
      this.primed2 = true
    }
    this.prev = value
    this.primed = true
  }

  adapt(u: number): void {
    this.sum = this.sum - (this.sum >>> RICE_ADAPT_SHIFT) + u
  }
}

function unzigzag(u: number): number {
  return (u >>> 1) ^ -(u & 1)
}

export function decodeFrame(frame: Uint8Array): DecodedFrame {
  if (frame.length !== FRAME_BYTES) {
    throw new FrameError(`la trama debe tener ${FRAME_BYTES} bytes, llegaron ${frame.length}`)
  }
  const header = readHeader(frame)
  if (header.magic !== FRAME_MAGIC) throw new FrameError('magic inválido')
  if (header.version !== FRAME_VERSION) throw new FrameError('versión desconocida')
  if (header.crc32 !== frameCrc(frame)) throw new FrameError('CRC-32 no coincide')
  if (header.streams === 0) throw new FrameError('streams = 0')
  if (header.runCount === 0 || header.runCount > MAX_FLAG_RUNS) {
    throw new FrameError('run_count fuera de rango')
  }
  if (HEADER_BYTES + header.bitBytes + header.runCount * RUN_BYTES > FRAME_BYTES) {
    throw new FrameError('el bitstream y las corridas se superponen')
  }
  if (header.nSamples === 0) throw new FrameError('n_samples = 0')

  const nChannels = header.includesDiagnostic ? header.streams / 2 : header.streams
  const runsOffset = FRAME_BYTES - header.runCount * RUN_BYTES
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  const reader = new BitReader(frame.subarray(HEADER_BYTES), header.bitBytes * 8)
  const streams = Array.from({ length: header.streams }, () => new RiceStream())

  const rawUV = Array.from({ length: nChannels }, () => new Int32Array(header.nSamples))
  const diagnosticUV = Array.from(
    { length: header.includesDiagnostic ? nChannels : 0 },
    () => new Int32Array(header.nSamples),
  )
  const flags = new Uint8Array(header.nSamples)
  const timestampsMs = new Uint32Array(header.nSamples)

  let runIdx = 0
  let runRemaining = view.getUint16(runsOffset + 1, true)

  for (let i = 0; i < header.nSamples; i++) {
    timestampsMs[i] = (header.t0Ms + i * STEP_MS) >>> 0

    for (let j = 0; j < header.streams; j++) {
      let q = 0
      while (q < RICE_ESCAPE_Q) {
        if (reader.readBit()) break
        q++
      }
      const k = streams[j].k()
      const u =
        q >= RICE_ESCAPE_Q
          ? reader.readBits(RICE_RAW_BITS)
          : (q << k) | (k > 0 ? reader.readBits(k) : 0)

      const value = streams[j].predict() + unzigzag(u)
      // Espejo exacto del codificador: la primera muestra no alimenta la media.
      const wasPrimed = streams[j].primed
      streams[j].push(value)
      if (wasPrimed) streams[j].adapt(u)

      if (j < nChannels) rawUV[j][i] = value
      else diagnosticUV[j - nChannels][i] = value
    }

    while (runRemaining === 0 && runIdx + 1 < header.runCount) {
      runIdx++
      runRemaining = view.getUint16(runsOffset + runIdx * RUN_BYTES + 1, true)
    }
    if (runRemaining === 0) throw new FrameError('corridas de flags inconsistentes')
    flags[i] = frame[runsOffset + runIdx * RUN_BYTES]
    runRemaining--
  }

  return { header, rawUV, diagnosticUV, flags, timestampsMs }
}
