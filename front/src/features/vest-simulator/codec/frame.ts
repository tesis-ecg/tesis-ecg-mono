/**
 * Formato de trama del Holter — constantes y cabecera.
 *
 * Port de `../Holter-ECG-System/include/config.h` y `EcgFrameCodec.h`. Si el
 * firmware cambia alguna de estas constantes, sube `FRAME_VERSION` y el
 * decodificador del backend rechaza lo que no entiende en vez de devolver
 * señal incorrecta.
 *
 * Todo little-endian, sin padding implícito.
 *
 * ```
 * [0..23]                     cabecera
 * [24 .. 24+bitBytes-1]       bitstream Rice (MSB primero)
 * [...ceros...]               relleno (entra en el CRC)
 * [256-runCount*3 .. 255]     corridas RLE de flags: uint8 flags + uint16 largo
 * ```
 */

export const FRAME_BYTES = 256
export const HEADER_BYTES = 24
export const CRC_OFFSET = 20
export const RUN_BYTES = 3
export const MAX_FLAG_RUNS = 24
export const FRAME_MAGIC = 0xec61
export const FRAME_VERSION = 1

export const PREDICTOR_ORDER = 2
export const RICE_ESCAPE_Q = 12
export const RICE_RAW_BITS = 23
export const RICE_ADAPT_SHIFT = 3
export const RICE_K_MAX = 24
export const RICE_SEED_MEAN = 8

export const SAMPLE_RATE_HZ = 500
/** División entera, igual que el firmware: 1000 / 500 = 2 ms. */
export const STEP_MS = Math.floor(1000 / SAMPLE_RATE_HZ)
export const BOOTID_MODULO = 16
/** `ECG_FRAME_MAX_SAMPLE_GAP_MS`: por encima de esto la trama se cierra con GAP. */
export const MAX_SAMPLE_GAP_MS = 6

// Bits de hdrFlags (byte 3)
export const HDR_REASON_MASK = 0x03
export const HDR_DIAGNOSTIC = 0x04
export const HDR_SIMULATED = 0x08
export const HDR_BOOTID_SHIFT = 4
export const HDR_BOOTID_MASK = 0xf0

/** Motivo de cierre de la trama. */
export const CLOSE_FULL = 0
export const CLOSE_GAP = 1
export const CLOSE_FLUSH = 2
export const CLOSE_RUNS = 3

// Bits de flags por muestra
export const FLAG_LEAD_OFF = 0x01
export const FLAG_R_PEAK = 0x02
export const FLAG_EVENT_MARKER = 0x04
export const FLAG_RLD_OFF = 0x08
export const FLAG_ADC_SATURATED = 0x10
export const FLAG_LEAD_OFF_CH2 = 0x20
export const FLAG_SQI_MASK = 0xc0
export const FLAG_SQI_SHIFT = 6

export const SQ_UNKNOWN = 0
export const SQ_BAD = 1
export const SQ_MARGINAL = 2
export const SQ_GOOD = 3

export interface FrameHeader {
  magic: number
  version: number
  closeReason: number
  includesDiagnostic: boolean
  simulated: boolean
  bootId: number
  seq: number
  t0Ms: number
  nSamples: number
  durationMs: number
  bitBytes: number
  runCount: number
  streams: number
  crc32: number
}

/**
 * Tabla del CRC-32 IEEE 802.3 (polinomio reflejado 0xEDB88320) — el mismo
 * `zlib.crc32` que usa el backend.
 */
const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let i = 0; i < 256; i++) {
    let crc = i
    for (let bit = 0; bit < 8; bit++) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1
    }
    table[i] = crc >>> 0
  }
  return table
})()

export function crc32(bytes: Uint8Array, seed = 0xffffffff): number {
  let crc = seed
  for (let i = 0; i < bytes.length; i++) {
    crc = (crc >>> 8) ^ CRC_TABLE[(crc ^ bytes[i]) & 0xff]
  }
  return crc >>> 0
}

/**
 * CRC de la trama entera **salteando los 4 bytes del propio CRC**.
 *
 * Cubre también el relleno en cero, así que detecta basura en la zona no usada
 * de la trama, no solo en los datos.
 */
export function frameCrc(frame: Uint8Array): number {
  const head = crc32(frame.subarray(0, CRC_OFFSET))
  return (crc32(frame.subarray(CRC_OFFSET + 4), head) ^ 0xffffffff) >>> 0
}

export function readHeader(frame: Uint8Array): FrameHeader {
  const view = new DataView(frame.buffer, frame.byteOffset, frame.byteLength)
  const hdr = frame[3]
  return {
    magic: view.getUint16(0, true),
    version: frame[2],
    closeReason: hdr & HDR_REASON_MASK,
    includesDiagnostic: (hdr & HDR_DIAGNOSTIC) !== 0,
    simulated: (hdr & HDR_SIMULATED) !== 0,
    bootId: (hdr & HDR_BOOTID_MASK) >> HDR_BOOTID_SHIFT,
    seq: view.getUint32(4, true),
    t0Ms: view.getUint32(8, true),
    nSamples: view.getUint16(12, true),
    durationMs: view.getUint16(14, true),
    bitBytes: view.getUint16(16, true),
    runCount: frame[18],
    streams: frame[19],
    crc32: view.getUint32(CRC_OFFSET, true),
  }
}

/** Reescribe `seq` recalculando el CRC. Deja la trama válida con otro número. */
export function withSeq(frame: Uint8Array, seq: number): Uint8Array {
  const patched = frame.slice()
  const view = new DataView(patched.buffer)
  view.setUint32(4, seq >>> 0, true)
  view.setUint32(CRC_OFFSET, frameCrc(patched), true)
  return patched
}

/** Rompe el CRC declarado sin tocar el resto: simula corrupción en tránsito. */
export function corruptCrc(frame: Uint8Array): Uint8Array {
  const broken = frame.slice()
  const view = new DataView(broken.buffer)
  view.setUint32(CRC_OFFSET, (view.getUint32(CRC_OFFSET, true) ^ 0xdeadbeef) >>> 0, true)
  return broken
}
