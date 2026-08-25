/**
 * Codificador Rice del Holter — port de `EcgFrameEncoder`
 * (`../Holter-ECG-System/include/EcgFrameCodec.h`, líneas 428-676).
 *
 * Codificador y decodificador actualizan el estado adaptativo con las MISMAS
 * operaciones enteras, por eso el parámetro `k` nunca hace falta transmitirlo y
 * las dos puntas no pueden divergir (no interviene ni un float).
 *
 * El detalle que más fácil se rompe al portar esto: **la primera muestra de
 * cada trama no alimenta la media adaptativa**. Su "residuo" es en realidad el
 * valor absoluto de la señal, así que no dice nada sobre la actividad del
 * tramo. Si entra, `k` se dispara y el resto de la trama se decodifica mal.
 */

import {
  CLOSE_FULL,
  CLOSE_GAP,
  CLOSE_RUNS,
  CRC_OFFSET,
  FRAME_BYTES,
  FRAME_MAGIC,
  FRAME_VERSION,
  HDR_BOOTID_SHIFT,
  HDR_DIAGNOSTIC,
  HDR_REASON_MASK,
  HDR_SIMULATED,
  HEADER_BYTES,
  MAX_FLAG_RUNS,
  MAX_SAMPLE_GAP_MS,
  PREDICTOR_ORDER,
  RICE_ADAPT_SHIFT,
  RICE_ESCAPE_Q,
  RICE_K_MAX,
  RICE_RAW_BITS,
  RICE_SEED_MEAN,
  RUN_BYTES,
  BOOTID_MODULO,
  frameCrc,
} from './frame'

export interface EcgSample {
  timestampMs: number
  /** Una entrada por derivación, en µV. int32: el front-end es DC-acoplado. */
  rawUV: number[]
  flags: number
  diagnosticUV?: number[]
}

function zigzag(v: number): number {
  return ((v << 1) ^ (v >> 31)) >>> 0
}

function riceK(mean: number): number {
  let k = 0
  while (k < RICE_K_MAX && 1 << (k + 1) <= mean) k++
  return k
}

function riceBits(u: number, k: number): number {
  const q = u >>> k
  if (q >= RICE_ESCAPE_Q) return RICE_ESCAPE_Q + RICE_RAW_BITS
  return q + 1 + k
}

/** Escritor MSB primero. **Precondición: el buffer viene en cero.** */
class BitWriter {
  private readonly buf: Uint8Array
  pos: number

  constructor(buf: Uint8Array, bitPos: number) {
    this.buf = buf
    this.pos = bitPos
  }

  writeBits(value: number, nbits: number): void {
    for (let i = nbits - 1; i >= 0; i--) {
      if ((value >>> i) & 1) this.buf[this.pos >> 3] |= 0x80 >> (this.pos & 7)
      this.pos++
    }
  }

  /** Los ceros salen gratis: el buffer ya está en cero y la posición solo avanza. */
  skipZeros(n: number): void {
    this.pos += n
  }
}

class RiceStream {
  prev = 0
  prev2 = 0
  sum = RICE_SEED_MEAN << RICE_ADAPT_SHIFT
  primed = false
  primed2 = false

  k(): number {
    return riceK(this.sum >>> RICE_ADAPT_SHIFT)
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

export interface FrameEncoderOptions {
  nChannels?: number
  storeDiagnostic?: boolean
  /** `hdrFlags` bit 3. Por default **true**: esto es un simulador de banco. */
  simulated?: boolean
  firstSeq?: number
  bootId?: number
}

export class FrameEncoder {
  readonly nChannels: number
  readonly storeDiagnostic: boolean
  readonly streamsCount: number
  readonly maxSamples: number
  simulated: boolean
  seq: number
  bootId: number

  private payload!: Uint8Array
  private bitPos = 0
  private nSamples = 0
  private runFlags: number[] = []
  private runLen: number[] = []
  private runCount = 0
  private t0Ms = 0
  private tLastMs = 0
  private closeReason = CLOSE_FULL
  private streams: RiceStream[] = []

  constructor(options: FrameEncoderOptions = {}) {
    this.nChannels = options.nChannels ?? 1
    this.storeDiagnostic = options.storeDiagnostic ?? false
    this.simulated = options.simulated ?? true
    this.streamsCount = this.nChannels * (this.storeDiagnostic ? 2 : 1)
    this.maxSamples = Math.floor(((FRAME_BYTES - HEADER_BYTES - RUN_BYTES) * 8) / this.streamsCount)
    this.seq = options.firstSeq ?? 0
    this.bootId = (options.bootId ?? 0) % BOOTID_MODULO
    this.startFrame()
  }

  get isEmpty(): boolean {
    return this.nSamples === 0
  }

  setBootId(bootId: number): void {
    this.bootId = ((bootId % BOOTID_MODULO) + BOOTID_MODULO) % BOOTID_MODULO
  }

  /**
   * `false` (sin tocar nada) si la muestra no entra: hay que cerrar la trama,
   * entregarla, y volver a llamar con la MISMA muestra.
   */
  addSample(sample: EcgSample): boolean {
    // 1. Continuidad temporal. Un salto grande significa que se perdieron
    //    muestras aguas arriba o que millis() dio la vuelta: la trama deja de
    //    ser una ventana uniformemente muestreada y el formato se apoya en que
    //    lo sea. Se cierra acá para que el hueco quede EXPLÍCITO entre dos
    //    tramas en vez de disolverse dentro de una que aparenta ser continua.
    if (this.nSamples > 0) {
      const dt = (sample.timestampMs - this.tLastMs) >>> 0
      if (dt > MAX_SAMPLE_GAP_MS) {
        this.closeReason = CLOSE_GAP
        return false
      }
      if ((sample.timestampMs - this.t0Ms) >>> 0 > 0xffff) {
        this.closeReason = CLOSE_GAP
        return false
      }
    }
    if (this.nSamples >= this.maxSamples) {
      this.closeReason = CLOSE_FULL
      return false
    }

    // 2. Corridas de flags (RLE).
    const needNewRun =
      this.runCount === 0 ||
      this.runFlags[this.runCount - 1] !== sample.flags ||
      this.runLen[this.runCount - 1] === 0xffff
    const newRunCount = this.runCount + (needNewRun ? 1 : 0)
    if (newRunCount > MAX_FLAG_RUNS) {
      this.closeReason = CLOSE_RUNS
      return false
    }

    // 3. Costo EXACTO en bits, antes de escribir un solo bit — por eso los
    //    `return false` de arriba dejan el estado intacto.
    const values = this.gather(sample)
    const us: number[] = []
    const ks: number[] = []
    let neededBits = 0
    for (let j = 0; j < this.streamsCount; j++) {
      const u = zigzag(values[j] - this.streams[j].predict())
      const k = this.streams[j].k()
      us.push(u)
      ks.push(k)
      neededBits += riceBits(u, k)
    }

    const availableBits = (FRAME_BYTES - HEADER_BYTES - newRunCount * RUN_BYTES) * 8
    if (this.bitPos + neededBits > availableBits) {
      this.closeReason = CLOSE_FULL
      return false
    }

    // 4. Commit.
    const writer = new BitWriter(this.payload, this.bitPos)
    for (let j = 0; j < this.streamsCount; j++) {
      const u = us[j]
      const k = ks[j]
      const q = u >>> k
      if (q >= RICE_ESCAPE_Q) {
        writer.skipZeros(RICE_ESCAPE_Q) // marca de escape
        writer.writeBits(u, RICE_RAW_BITS)
      } else {
        writer.skipZeros(q) // prefijo unario
        writer.writeBits(1, 1) // terminador
        if (k > 0) writer.writeBits(u & ((1 << k) - 1), k)
      }
      const wasPrimed = this.streams[j].primed
      this.streams[j].push(values[j])
      if (wasPrimed) this.streams[j].adapt(u)
    }
    this.bitPos = writer.pos

    if (needNewRun) {
      this.runFlags.push(sample.flags)
      this.runLen.push(1)
      this.runCount = newRunCount
    } else {
      this.runLen[this.runCount - 1]++
    }

    if (this.nSamples === 0) this.t0Ms = sample.timestampMs
    this.tLastMs = sample.timestampMs
    this.nSamples++
    return true
  }

  /** Serializa la trama y arranca una nueva. `null` si no había muestras. */
  close(reason?: number): Uint8Array | null {
    if (this.nSamples === 0) return null

    const closeReason = reason ?? this.closeReason
    const bitBytes = Math.ceil(this.bitPos / 8)

    const frame = new Uint8Array(FRAME_BYTES)
    frame.set(this.payload, HEADER_BYTES)
    const view = new DataView(frame.buffer)

    view.setUint16(0, FRAME_MAGIC, true)
    frame[2] = FRAME_VERSION
    frame[3] =
      (closeReason & HDR_REASON_MASK) |
      (this.storeDiagnostic ? HDR_DIAGNOSTIC : 0) |
      (this.simulated ? HDR_SIMULATED : 0) |
      ((this.bootId << HDR_BOOTID_SHIFT) & 0xf0)
    view.setUint32(4, this.seq >>> 0, true)
    view.setUint32(8, this.t0Ms >>> 0, true)
    view.setUint16(12, this.nSamples, true)
    view.setUint16(14, (this.tLastMs - this.t0Ms) & 0xffff, true)
    view.setUint16(16, bitBytes, true)
    frame[18] = this.runCount
    frame[19] = this.streamsCount

    const runsOffset = FRAME_BYTES - this.runCount * RUN_BYTES
    for (let i = 0; i < this.runCount; i++) {
      frame[runsOffset + i * RUN_BYTES] = this.runFlags[i]
      view.setUint16(runsOffset + i * RUN_BYTES + 1, this.runLen[i], true)
    }

    view.setUint32(CRC_OFFSET, frameCrc(frame), true)

    this.seq = (this.seq + 1) >>> 0
    this.startFrame()
    return frame
  }

  private startFrame(): void {
    // Arrancar en cero no es cosmético: el BitWriter cuenta con que los bits que
    // no escribe ya son 0 (así el prefijo unario es gratis), y el CRC cubre la
    // zona de relleno, así que tiene que ser determinística.
    this.payload = new Uint8Array(FRAME_BYTES - HEADER_BYTES)
    this.bitPos = 0
    this.nSamples = 0
    this.runCount = 0
    this.runFlags = []
    this.runLen = []
    this.t0Ms = 0
    this.tLastMs = 0
    this.closeReason = CLOSE_FULL
    this.streams = Array.from({ length: this.streamsCount }, () => new RiceStream())
  }

  /** Orden: primero los raw de cada canal, después los diagnostic. */
  private gather(sample: EcgSample): number[] {
    const values = sample.rawUV.slice(0, this.nChannels)
    while (values.length < this.nChannels) values.push(0)
    if (this.storeDiagnostic) {
      const diagnostic = (sample.diagnosticUV ?? []).slice(0, this.nChannels)
      while (diagnostic.length < this.nChannels) diagnostic.push(0)
      values.push(...diagnostic)
    }
    return values
  }
}

/** Codifica muestras en tantas tramas de 256 B como haga falta. */
export function encodeSamples(
  samples: Iterable<EcgSample>,
  options: FrameEncoderOptions = {},
): Uint8Array[] {
  const encoder = new FrameEncoder(options)
  const frames: Uint8Array[] = []
  for (const sample of samples) {
    if (!encoder.addSample(sample)) {
      const frame = encoder.close()
      if (frame) frames.push(frame)
      if (!encoder.addSample(sample)) {
        throw new Error('la muestra no entra ni en una trama vacía')
      }
    }
  }
  const last = encoder.close()
  if (last) frames.push(last)
  return frames
}
