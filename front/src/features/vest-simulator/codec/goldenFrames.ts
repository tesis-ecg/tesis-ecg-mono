/**
 * Definición del fixture dorado, compartida por el test que lo verifica y por
 * el modo de regeneración.
 *
 * El tramo ejercita todo lo que el decodificador tiene que soportar: latidos
 * normales, lead-off, tierra suelta, saturación del ADC, un tramo no
 * analizable, marcas de síntoma del paciente y un offset de continua fuera del
 * rango de int16.
 */

import { encodeSamples } from './riceEncoder'
import { DEFAULT_SIGNAL_CONFIG, generateSignal, type SignalConfig } from './signal'

export const GOLDEN_CONFIG: SignalConfig = {
  ...DEFAULT_SIGNAL_CONFIG,
  seed: 20260821,
  durationSec: 30,
  baseBpm: 68,
  bpmVariability: 8,
  baselineOffsetUV: 120_000,
  leadOffSpans: [{ startSec: 6, durationSec: 3 }],
  rldOffSpans: [{ startSec: 12, durationSec: 2 }],
  saturatedSpans: [{ startSec: 18, durationSec: 1 }],
  unanalyzableSpans: [{ startSec: 22, durationSec: 2 }],
  symptomMarkersSec: [10, 25],
}

export const GOLDEN_FIRST_SEQ = 1000
export const GOLDEN_BOOT_ID = 5

export interface GoldenFixture {
  frames: Uint8Array[]
  binary: Uint8Array
  rawUV: number[]
  flags: number[]
}

export function buildGoldenFixture(): GoldenFixture {
  const { samples } = generateSignal(GOLDEN_CONFIG)
  const frames = encodeSamples(samples, {
    firstSeq: GOLDEN_FIRST_SEQ,
    bootId: GOLDEN_BOOT_ID,
    simulated: true,
  })
  const binary = new Uint8Array(frames.length * 256)
  frames.forEach((frame, i) => binary.set(frame, i * 256))
  return {
    frames,
    binary,
    rawUV: samples.map((s) => s.rawUV[0]),
    flags: samples.map((s) => s.flags),
  }
}
